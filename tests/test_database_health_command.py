"""Tests for /database health (Rotation & Collection Health): collection
selection (thread default + switch), the authoritative eligibility
breakdown, reconciliation identities, Next Vote / Low Pool status, and
the "must never modify rotation state" guarantee.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import handle_database_health
from watch_party_manager.domain.guild_configuration import GuildConfiguration, VotingDefaultsConfig
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionDatabaseConfiguration,
    SuggestionRulesConfig,
)
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
CHANNEL_ID = 200
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, role_ids=()) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None
        self.edited_content = None
        self.edited_view = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def edit_message(self, content=None, view=None) -> None:
        self.edited_content = content
        self.edited_view = view


class FakeInteraction:
    def __init__(self, user=None, guild_id=GUILD_ID, channel_id=CHANNEL_ID, guild=None) -> None:
        self.user = user if user is not None else FakeMember([WASH_CREW_ROLE_ID])
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.guild = guild
        self.response = FakeResponse()


class FakeGuildConfigurationRepository:
    def __init__(self, configuration=None) -> None:
        self._configuration = configuration

    def get(self, guild_id: int):
        return self._configuration


class FakeBot:
    def __init__(self, suggestion_service, rotation_repository=None, guild_configuration=None) -> None:
        self.suggestion_service = suggestion_service
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID
        )
        self.wash_crew_role_id = WASH_CREW_ROLE_ID
        self.suggestion_database_configuration_repository = None
        self.guild_configuration_repository = FakeGuildConfigurationRepository(guild_configuration)
        self.rotation_service = RotationService(suggestion_service, repository=rotation_repository)
        self.collection_eligibility_service = CollectionEligibilityService(suggestion_service, self.rotation_service)


class DatabaseHealthCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.bot = FakeBot(
            self.suggestion_service, rotation_repository=JsonRotationRepository(root / "rotations.json")
        )
        self.bot.suggestion_database_configuration_repository = self.configuration_repository
        self.database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database

    def _set_candidate_selection(self, mode: CandidateSelectionMode) -> None:
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=self.database.database_id,
                display_name="Movie Night",
                suggestion_rules=SuggestionRulesConfig(candidate_selection=mode),
            )
        )


class PermissionTests(DatabaseHealthCommandTestCase):
    async def test_non_wash_crew_is_rejected(self) -> None:
        interaction = FakeInteraction(user=FakeMember([WATCH_PARTY_MEMBER_ROLE_ID]))

        await handle_database_health(interaction, self.bot)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_unconfigured_wash_crew_role_fails_closed(self) -> None:
        self.bot.wash_crew_role_id = None
        self.bot.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=None
        )
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIn("WASH_CREW_ROLE_ID", interaction.response.sent_message)


class CollectionSelectionTests(DatabaseHealthCommandTestCase):
    async def test_uses_the_thread_matched_database_automatically(self) -> None:
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIn("Movie Night", interaction.response.sent_message)

    async def test_prompts_when_multiple_databases_and_none_match(self) -> None:
        self.suggestion_service.create_database("TV Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1)
        interaction = FakeInteraction(channel_id=999999)

        await handle_database_health(interaction, self.bot)

        self.assertIsNotNone(interaction.response.sent_view)
        self.assertNotIn("Total Watch Items", interaction.response.sent_message or "")

    async def test_offers_a_switch_button_with_multiple_databases(self) -> None:
        self.suggestion_service.create_database("TV Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1)
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        custom_ids = [item.custom_id for item in interaction.response.sent_view.children]
        self.assertIn("wpm_list_switch_collection", custom_ids)

    async def test_no_switch_button_with_only_one_database(self) -> None:
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIsNone(interaction.response.sent_view)

    async def test_switching_shows_the_other_collections_health(self) -> None:
        self.suggestion_service.create_database("TV Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1)
        interaction = FakeInteraction()
        await handle_database_health(interaction, self.bot)
        switch_button = next(
            item for item in interaction.response.sent_view.children
            if item.custom_id == "wpm_list_switch_collection"
        )

        switch_interaction = FakeInteraction()
        await switch_button.callback(interaction=switch_interaction)
        select = switch_interaction.response.edited_view.children[0]
        other_database_id = next(int(option.value) for option in select.options if "TV Night" in option.label)
        select._values = [str(other_database_id)]
        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        self.assertIn("TV Night", select_interaction.response.edited_content)


class ReportContentTests(DatabaseHealthCommandTestCase):
    async def test_reconciliation_identities_hold(self) -> None:
        item_a = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        item_b = self.suggestion_service.suggest("The Matrix", database_id=self.database.database_id).watch_item
        item_c = self.suggestion_service.suggest("Inception", database_id=self.database.database_id).watch_item
        self.suggestion_service.suggest("Arrival", database_id=self.database.database_id)
        self.suggestion_service.record_vote_win(item_a.id, date.today())
        self.suggestion_service.reject_suggestion(item_b.id, 1)
        self.suggestion_service.reject_suggestion(item_b.id, 2)
        self.bot.rotation_service.get_or_start_rotation(self.database.database_id)
        self.bot.rotation_service.record_presentation(self.database.database_id, [item_c.id])
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        message = interaction.response.sent_message
        self.assertIn("Total Watch Items: 4", message)
        self.assertIn("Active Watch Items: 2 (Eligible for Voting + Rotation Cooldown)", message)
        self.assertIn("Eligible for Voting: 1", message)
        self.assertIn("Rotation Cooldown: 1", message)
        self.assertIn("Vote Winners: 1", message)
        self.assertIn("Retired: 1", message)

    async def test_each_status_line_uses_the_same_emoji_list_uses_for_that_status(self) -> None:
        # Visual Consistency: /database health previously had no
        # color-coded indicators at all -- these must match /list's own
        # SUGGESTION_DISPLAY_STATUS_EMOJI exactly, not a new visual
        # system invented just for this command.
        item_a = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        item_b = self.suggestion_service.suggest("The Matrix", database_id=self.database.database_id).watch_item
        item_c = self.suggestion_service.suggest("Inception", database_id=self.database.database_id).watch_item
        self.suggestion_service.suggest("Arrival", database_id=self.database.database_id)
        self.suggestion_service.record_vote_win(item_a.id, date.today())
        self.suggestion_service.reject_suggestion(item_b.id, 1)
        self.suggestion_service.reject_suggestion(item_b.id, 2)
        self.bot.rotation_service.get_or_start_rotation(self.database.database_id)
        self.bot.rotation_service.record_presentation(self.database.database_id, [item_c.id])
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        message = interaction.response.sent_message
        self.assertIn("🟢 Eligible for Voting: 1", message)
        self.assertIn("🟡 Rotation Cooldown: 1", message)
        self.assertIn("🏆 Vote Winners: 1", message)
        self.assertIn("🗄️ Retired: 1", message)

    async def test_watched_count_appears_and_is_included_in_the_total(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.suggestion_service.suggest("The Matrix", database_id=self.database.database_id)
        self.suggestion_service.mark_suggestion_watched(item.id, date.today())
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        message = interaction.response.sent_message
        self.assertIn("✅ Watched: 1", message)
        self.assertIn("Total Watch Items: 2", message)

    async def test_never_modifies_rotation_state(self) -> None:
        self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIsNone(self.bot.rotation_service.get_open_rotation(self.database.database_id))

    async def test_next_vote_is_ready_when_eligible_meets_the_configured_count(self) -> None:
        self.bot.guild_configuration_repository = FakeGuildConfigurationRepository(
            GuildConfiguration(guild_id=GUILD_ID, guild_name="Test", voting_defaults=VotingDefaultsConfig(candidate_count=2))
        )
        self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        self.suggestion_service.suggest("The Matrix", database_id=self.database.database_id)
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIn("Next Vote: 🟢 Ready", interaction.response.sent_message)

    async def test_next_vote_needs_rollover_when_active_covers_it_but_eligible_does_not(self) -> None:
        self.bot.guild_configuration_repository = FakeGuildConfigurationRepository(
            GuildConfiguration(guild_id=GUILD_ID, guild_name="Test", voting_defaults=VotingDefaultsConfig(candidate_count=2))
        )
        item_a = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.suggestion_service.suggest("The Matrix", database_id=self.database.database_id)
        self.bot.rotation_service.get_or_start_rotation(self.database.database_id)
        self.bot.rotation_service.record_presentation(self.database.database_id, [item_a.id])
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIn("Next Vote: 🟡 Needs Rollover", interaction.response.sent_message)

    async def test_next_vote_is_insufficient_when_the_whole_collection_is_too_small(self) -> None:
        self.bot.guild_configuration_repository = FakeGuildConfigurationRepository(
            GuildConfiguration(guild_id=GUILD_ID, guild_name="Test", voting_defaults=VotingDefaultsConfig(candidate_count=5))
        )
        self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIn("Next Vote: 🔴 Insufficient Suggestions", interaction.response.sent_message)

    async def test_low_pool_status_healthy_well_above_threshold(self) -> None:
        self.bot.guild_configuration_repository = FakeGuildConfigurationRepository(
            GuildConfiguration(guild_id=GUILD_ID, guild_name="Test", voting_defaults=VotingDefaultsConfig(candidate_count=2))
        )
        for title in ("Alien", "The Matrix", "Inception", "Arrival", "Amelie"):
            self.suggestion_service.suggest(title, database_id=self.database.database_id)
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIn("Low Pool Status: 🟢 Healthy", interaction.response.sent_message)

    async def test_low_pool_status_insufficient_below_candidate_count(self) -> None:
        self.bot.guild_configuration_repository = FakeGuildConfigurationRepository(
            GuildConfiguration(guild_id=GUILD_ID, guild_name="Test", voting_defaults=VotingDefaultsConfig(candidate_count=5))
        )
        self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIn("Low Pool Status: 🔴 Insufficient", interaction.response.sent_message)

    async def test_infinite_pool_shows_no_rotation_progress(self) -> None:
        self._set_candidate_selection(CandidateSelectionMode.INFINITE_POOL)
        self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIn("N/A (Pure Random selection has no rotation)", interaction.response.sent_message)
        self.assertIsNone(self.bot.rotation_service.get_open_rotation(self.database.database_id))

    async def test_rotation_progress_line_reflects_presented_count(self) -> None:
        item_a = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.suggestion_service.suggest("The Matrix", database_id=self.database.database_id)
        self.bot.rotation_service.get_or_start_rotation(self.database.database_id)
        self.bot.rotation_service.record_presentation(self.database.database_id, [item_a.id])
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIn("1 of 2 active items have been presented", interaction.response.sent_message)

    async def test_rotation_progress_line_shows_the_collections_own_rotation_number(self) -> None:
        # UI Polish (Rotation Information): Rotation.id is a single
        # counter shared across every collection in the server, not a
        # per-collection sequence -- the report must show this
        # collection's own 1st/2nd/... rotation number, not a raw,
        # potentially-large global id.
        item_a = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.suggestion_service.suggest("The Matrix", database_id=self.database.database_id)
        self.bot.rotation_service.get_or_start_rotation(self.database.database_id)
        self.bot.rotation_service.record_presentation(self.database.database_id, [item_a.id])
        self.bot.rotation_service.begin_next_rotation(self.database.database_id)
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIn("Rotation 2 Progress:", interaction.response.sent_message)

    async def test_rotation_number_is_scoped_per_collection_not_global(self) -> None:
        # A second collection's rotations must not shift the first
        # collection's own numbering.
        other = self.suggestion_service.create_database(
            "TV Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1
        ).database
        self.suggestion_service.suggest("Breaking Bad", database_id=other.database_id)
        self.bot.rotation_service.get_or_start_rotation(other.database_id)  # a rotation for the OTHER collection first

        self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        self.bot.rotation_service.get_or_start_rotation(self.database.database_id)
        interaction = FakeInteraction()

        await handle_database_health(interaction, self.bot)

        self.assertIn("Rotation 1 Progress:", interaction.response.sent_message)


class MultipleCollectionsAndRestartTests(DatabaseHealthCommandTestCase):
    async def test_two_collections_report_independently(self) -> None:
        other = self.suggestion_service.create_database(
            "TV Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1
        ).database
        self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        self.suggestion_service.suggest("Breaking Bad", database_id=other.database_id)
        self.suggestion_service.suggest("The Wire", database_id=other.database_id)

        first_interaction = FakeInteraction()
        await handle_database_health(first_interaction, self.bot)
        second_interaction = FakeInteraction(channel_id=CHANNEL_ID + 1)
        await handle_database_health(second_interaction, self.bot)

        self.assertIn("Total Watch Items: 1", first_interaction.response.sent_message)
        self.assertIn("Total Watch Items: 2", second_interaction.response.sent_message)

    async def test_health_report_survives_a_simulated_restart(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.bot.rotation_service.get_or_start_rotation(self.database.database_id)
        self.bot.rotation_service.record_presentation(self.database.database_id, [item.id])

        root = Path(self._temp_dir.name)
        restarted_suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        restarted_bot = FakeBot(
            restarted_suggestion_service, rotation_repository=JsonRotationRepository(root / "rotations.json")
        )
        restarted_bot.suggestion_database_configuration_repository = self.configuration_repository
        interaction = FakeInteraction()

        await handle_database_health(interaction, restarted_bot)

        self.assertIn("Rotation Cooldown: 1", interaction.response.sent_message)
        self.assertIn("Eligible for Voting: 0", interaction.response.sent_message)


if __name__ == "__main__":
    unittest.main()
