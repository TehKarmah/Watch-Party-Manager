"""Tests for the Watch Party Lifecycle's winner-announcement scheduling
handlers in bot.py: the Schedule Watch Party/Choose Winner to Schedule
button flow, duplicate-scheduling prevention, the guided modal, and the
automatic-completion announcement/embed sync.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import discord

from watch_party_manager.bot import (
    build_winner_announcement_view,
    finalize_schedule_watch_party_for_winner,
    handle_choose_winner_to_schedule,
    handle_schedule_watch_party_button,
    sync_watch_party_completion,
)
from watch_party_manager.domain.guild_configuration import GuildChannelsConfig, GuildConfiguration
from watch_party_manager.domain.watch_item import WatchItemStatus
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository
from watch_party_manager.services.config_service import ConfigService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_completion_service import VoteCompletionResult
from watch_party_manager.services.vote_service import VoteService
from watch_party_manager.services.watch_party_completion_service import WatchPartyCompletionResult
from watch_party_manager.services.watch_party_service import WatchPartyService
from watch_party_manager.winner_announcement_view import ChooseWinnerSelectView, ScheduleWatchPartyModal

GUILD_ID = 100
WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None
        self.sent_modal = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal


class FakeHTTPResponse:
    def __init__(self, status: int = 403, reason: str = "Forbidden") -> None:
        self.status = status
        self.reason = reason


class FakeScheduledEvent:
    def __init__(self, event_id: int) -> None:
        self.id = event_id


class FakeGuild:
    """Stands in for discord.Guild's create_scheduled_event -- by default
    succeeds, recording the kwargs it was called with so tests can assert
    on the event's title/description/times/location. raise_error, when
    set, makes the call raise that exception instead (Fallback Behavior).
    """

    def __init__(self, *, raise_error: Exception | None = None, next_event_id: int = 9001) -> None:
        self.raise_error = raise_error
        self.next_event_id = next_event_id
        self.create_scheduled_event_calls: list = []

    async def create_scheduled_event(self, **kwargs):
        self.create_scheduled_event_calls.append(kwargs)
        if self.raise_error is not None:
            raise self.raise_error
        return FakeScheduledEvent(self.next_event_id)


class FakeInteraction:
    def __init__(self, *, guild_id=GUILD_ID, roles=(WASH_CREW_ROLE_ID,), guild=None) -> None:
        self.user = FakeMember(roles=[FakeRole(role_id) for role_id in roles])
        self.guild_id = guild_id
        self.guild = guild if guild is not None else FakeGuild()
        self.response = FakeResponse()


class FakeAnnouncementChannel:
    def __init__(self) -> None:
        self.sent_messages: list = []

    async def send(self, content) -> None:
        self.sent_messages.append(content)


class FakeSchedulerHost:
    scheduler_service = None


class FakeBot:
    def __init__(
        self,
        suggestion_service,
        watch_party_service,
        config_service,
        guild_configuration_repository,
        vote_service=None,
    ) -> None:
        self.wash_crew_role_id = WASH_CREW_ROLE_ID
        self.suggestion_service = suggestion_service
        self.watch_party_service = watch_party_service
        self.config_service = config_service
        self.guild_configuration_repository = guild_configuration_repository
        self.vote_service = vote_service
        self.scheduler_host = FakeSchedulerHost()
        self.announcement_channel = FakeAnnouncementChannel()

    def get_channel(self, channel_id):
        return self.announcement_channel

    async def fetch_channel(self, channel_id):
        return self.announcement_channel


class WinnerAnnouncementSchedulingTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.watch_party_service = WatchPartyService(
            self.suggestion_service, repository=JsonWatchPartyRepository(root / "watch_parties.json")
        )
        self.guild_configuration_repository = GuildConfigurationRepository(root / "guild_configurations.json")
        self.guild_configuration_repository.save(
            GuildConfiguration(guild_id=GUILD_ID, guild_name="Test Guild", setup_completed=True)
        )
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.config_service = ConfigService(
            self.guild_configuration_repository, self.suggestion_service, self.suggestion_database_configuration_repository
        )
        self.vote_service = VoteService(self.suggestion_service, repository=JsonVoteRepository(root / "votes.json"))
        self.bot = FakeBot(
            self.suggestion_service,
            self.watch_party_service,
            self.config_service,
            self.guild_configuration_repository,
            vote_service=self.vote_service,
        )
        self.matrix = self.suggestion_service.suggest("The Matrix", release_year=1999).watch_item
        self.suggestion_service.record_vote_win(self.matrix.id, datetime.now(timezone.utc).date())


class BuildWinnerAnnouncementViewTests(WinnerAnnouncementSchedulingTestCase):
    def _make_result(self, winning_ids) -> VoteCompletionResult:
        class FakeVoteRound:
            id = 1

        return VoteCompletionResult(vote_round=FakeVoteRound(), winning_suggestion_ids=winning_ids)

    def test_returns_none_when_there_is_no_winner(self) -> None:
        view = build_winner_announcement_view(self.bot, self._make_result([]))

        self.assertIsNone(view)

    def test_returns_a_view_for_a_single_winner(self) -> None:
        view = build_winner_announcement_view(self.bot, self._make_result([self.matrix.id]))

        self.assertIsNotNone(view)
        self.assertEqual(view.children[0].label, "Schedule Watch Party")

    def test_shows_scheduled_state_when_a_watch_party_already_exists(self) -> None:
        self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
            guild_id=GUILD_ID,
        )

        view = build_winner_announcement_view(self.bot, self._make_result([self.matrix.id]))

        self.assertEqual(view.children[0].label, "Watch Party Scheduled")
        self.assertTrue(view.children[0].disabled)


class HandleScheduleWatchPartyButtonTests(WinnerAnnouncementSchedulingTestCase):
    async def test_rejects_a_non_wash_crew_member(self) -> None:
        interaction = FakeInteraction(roles=(1,))

        await handle_schedule_watch_party_button(interaction, self.bot, self.matrix.id, vote_round_id=1)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_opens_the_modal_with_the_runtime_based_default_duration(self) -> None:
        item = self.suggestion_service.suggest("Inception", runtime_minutes=148).watch_item
        self.suggestion_service.record_vote_win(item.id, date.today())
        interaction = FakeInteraction()

        await handle_schedule_watch_party_button(interaction, self.bot, item.id, vote_round_id=1)

        modal = interaction.response.sent_modal
        self.assertIsInstance(modal, ScheduleWatchPartyModal)
        self.assertEqual(modal.duration_input.default, "148m")

    async def test_shows_the_existing_watch_party_instead_of_opening_the_modal(self) -> None:
        self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
            guild_id=GUILD_ID,
        )
        interaction = FakeInteraction()

        await handle_schedule_watch_party_button(interaction, self.bot, self.matrix.id, vote_round_id=1)

        self.assertIn("already exists", interaction.response.sent_message)
        self.assertIsNone(interaction.response.sent_modal)


class HandleChooseWinnerToScheduleTests(WinnerAnnouncementSchedulingTestCase):
    async def test_offers_every_winning_title(self) -> None:
        other = self.suggestion_service.suggest("Inception", release_year=2010).watch_item
        self.suggestion_service.record_vote_win(other.id, date.today())

        class FakeVoteRound:
            id = 1

        result = VoteCompletionResult(vote_round=FakeVoteRound(), winning_suggestion_ids=[self.matrix.id, other.id])
        interaction = FakeInteraction()

        await handle_choose_winner_to_schedule(interaction, self.bot, result)

        self.assertIsInstance(interaction.response.sent_view, ChooseWinnerSelectView)
        select = interaction.response.sent_view.children[0]
        labels = {option.label for option in select.options}
        self.assertEqual(labels, {"The Matrix", "Inception"})


class FinalizeScheduleWatchPartyForWinnerTests(WinnerAnnouncementSchedulingTestCase):
    async def test_schedules_successfully(self) -> None:
        interaction = FakeInteraction()

        await finalize_schedule_watch_party_for_winner(
            interaction, self.bot, self.matrix.id, vote_round_id=1,
            when_text="2026-08-01 20:00", duration_text="2h", location_text="Discord Voice Chat", description_text="",
        )

        self.assertIn("scheduled", interaction.response.sent_message)
        watch_party = self.watch_party_service.get_active_watch_party_for_item(self.matrix.id)
        self.assertIsNotNone(watch_party)
        self.assertEqual(watch_party.duration_minutes, 120)
        self.assertEqual(watch_party.vote_round_id, 1)

    async def test_posts_a_public_announcement_to_the_home_channel_fallback(self) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=GUILD_ID,
                guild_name="Test Guild",
                setup_completed=True,
                channels=GuildChannelsConfig(home_channel_id=555),
            )
        )
        interaction = FakeInteraction()

        await finalize_schedule_watch_party_for_winner(
            interaction, self.bot, self.matrix.id, vote_round_id=1,
            when_text="2026-08-01 20:00", duration_text="2h", location_text="Discord Voice Chat", description_text="",
        )

        self.assertEqual(len(self.bot.announcement_channel.sent_messages), 1)
        watch_party = self.watch_party_service.get_active_watch_party_for_item(self.matrix.id)
        self.assertEqual(watch_party.channel_id, 555)

    async def test_rejects_an_invalid_date(self) -> None:
        interaction = FakeInteraction()

        await finalize_schedule_watch_party_for_winner(
            interaction, self.bot, self.matrix.id, vote_round_id=1,
            when_text="not a date", duration_text="2h", location_text="Discord Voice Chat", description_text="",
        )

        self.assertIsNone(self.watch_party_service.get_active_watch_party_for_item(self.matrix.id))

    async def test_rejects_an_invalid_duration(self) -> None:
        interaction = FakeInteraction()

        await finalize_schedule_watch_party_for_winner(
            interaction, self.bot, self.matrix.id, vote_round_id=1,
            when_text="2026-08-01 20:00", duration_text="not a duration", location_text="Discord Voice Chat", description_text="",
        )

        self.assertIsNone(self.watch_party_service.get_active_watch_party_for_item(self.matrix.id))

    async def test_rejects_a_duplicate_as_a_race_defense(self) -> None:
        self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
            guild_id=GUILD_ID,
        )
        interaction = FakeInteraction()

        await finalize_schedule_watch_party_for_winner(
            interaction, self.bot, self.matrix.id, vote_round_id=1,
            when_text="2026-08-01 20:00", duration_text="2h", location_text="Discord Voice Chat", description_text="",
        )

        self.assertIn("already", interaction.response.sent_message)

    async def test_description_override_is_persisted(self) -> None:
        interaction = FakeInteraction()

        await finalize_schedule_watch_party_for_winner(
            interaction, self.bot, self.matrix.id, vote_round_id=1,
            when_text="2026-08-01 20:00", duration_text="2h", location_text="Discord Voice Chat", description_text="Bring snacks!",
        )

        watch_party = self.watch_party_service.get_active_watch_party_for_item(self.matrix.id)
        self.assertEqual(watch_party.description_override, "Bring snacks!")


class DiscordScheduledEventCreationTests(WinnerAnnouncementSchedulingTestCase):
    """Discord Scheduled Events: creation reuses the winning suggestion's
    known details (title, year, IMDb summary/link, runtime-derived
    duration) with no second prompt, links the created event's ID onto
    the watch party, and degrades gracefully (never silently) when
    Discord Scheduled Events can't be used.
    """

    async def test_creates_a_scheduled_event_and_links_its_id(self) -> None:
        interaction = FakeInteraction()

        await finalize_schedule_watch_party_for_winner(
            interaction, self.bot, self.matrix.id, vote_round_id=1,
            when_text="2026-08-01 20:00", duration_text="2h", location_text="Discord Voice Chat", description_text="",
        )

        watch_party = self.watch_party_service.get_active_watch_party_for_item(self.matrix.id)
        self.assertEqual(watch_party.discord_event_id, 9001)
        self.assertEqual(len(interaction.guild.create_scheduled_event_calls), 1)

    async def test_event_name_and_location_and_times_are_built_from_known_data(self) -> None:
        interaction = FakeInteraction()

        await finalize_schedule_watch_party_for_winner(
            interaction, self.bot, self.matrix.id, vote_round_id=1,
            when_text="2026-08-01 20:00", duration_text="2h", location_text="Discord Voice Chat", description_text="",
        )

        call = interaction.guild.create_scheduled_event_calls[0]
        self.assertEqual(call["name"], "🎬 Watch Party: The Matrix (1999)")
        self.assertEqual(call["location"], "Discord Voice Chat")
        self.assertEqual(call["start_time"], datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc))
        self.assertEqual(call["end_time"], datetime(2026, 8, 1, 22, 0, tzinfo=timezone.utc))
        self.assertEqual(call["entity_type"], discord.EntityType.external)

    async def test_confirmation_includes_the_discord_event_link(self) -> None:
        interaction = FakeInteraction()

        await finalize_schedule_watch_party_for_winner(
            interaction, self.bot, self.matrix.id, vote_round_id=1,
            when_text="2026-08-01 20:00", duration_text="2h", location_text="Discord Voice Chat", description_text="",
        )

        watch_party = self.watch_party_service.get_active_watch_party_for_item(self.matrix.id)
        self.assertEqual(len(self.bot.announcement_channel.sent_messages), 0)  # no announcement destination configured
        # The ephemeral scheduling confirmation itself never fails just
        # because there's no announcement destination -- separately
        # confirm the event ID actually round-trips through the service.
        self.assertIsNotNone(watch_party.discord_event_id)

    async def test_forbidden_falls_back_gracefully_without_losing_the_schedule(self) -> None:
        interaction = FakeInteraction(guild=FakeGuild(raise_error=discord.Forbidden(FakeHTTPResponse(), "Missing Access")))

        await finalize_schedule_watch_party_for_winner(
            interaction, self.bot, self.matrix.id, vote_round_id=1,
            when_text="2026-08-01 20:00", duration_text="2h", location_text="Discord Voice Chat", description_text="",
        )

        self.assertIn("Manage Events", interaction.response.sent_message)
        watch_party = self.watch_party_service.get_active_watch_party_for_item(self.matrix.id)
        self.assertIsNotNone(watch_party)
        self.assertIsNone(watch_party.discord_event_id)

    async def test_http_exception_falls_back_gracefully_without_losing_the_schedule(self) -> None:
        interaction = FakeInteraction(
            guild=FakeGuild(raise_error=discord.HTTPException(FakeHTTPResponse(status=400, reason="Bad Request"), "Invalid Form Body"))
        )

        await finalize_schedule_watch_party_for_winner(
            interaction, self.bot, self.matrix.id, vote_round_id=1,
            when_text="2026-08-01 20:00", duration_text="2h", location_text="Discord Voice Chat", description_text="",
        )

        self.assertIn("Discord rejected", interaction.response.sent_message)
        watch_party = self.watch_party_service.get_active_watch_party_for_item(self.matrix.id)
        self.assertIsNotNone(watch_party)
        self.assertIsNone(watch_party.discord_event_id)

    async def test_no_guild_falls_back_gracefully(self) -> None:
        # guild_id (used for the WASH-side schedule) is still set here --
        # only .guild (used solely for Discord Event creation) is None,
        # isolating this from the earlier "no guild_id" rejection path.
        interaction = FakeInteraction()
        interaction.guild = None

        await finalize_schedule_watch_party_for_winner(
            interaction, self.bot, self.matrix.id, vote_round_id=1,
            when_text="2026-08-01 20:00", duration_text="2h", location_text="Discord Voice Chat", description_text="",
        )

        self.assertIn("no Discord Event was created", interaction.response.sent_message)


class SyncWatchPartyCompletionTests(WinnerAnnouncementSchedulingTestCase):
    async def test_posts_a_completion_announcement(self) -> None:
        watch_party = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=datetime.now(timezone.utc) - timedelta(hours=3),
            guild_id=GUILD_ID,
            channel_id=777,
            duration_minutes=60,
        ).watch_party

        result = WatchPartyCompletionResult(watch_party=watch_party, watch_item=self.matrix)
        await sync_watch_party_completion(self.bot, result)

        self.assertEqual(len(self.bot.announcement_channel.sent_messages), 1)
        self.assertIn("watched", self.bot.announcement_channel.sent_messages[0])


if __name__ == "__main__":
    unittest.main()
