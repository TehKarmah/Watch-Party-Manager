"""End-to-end regression tests for the release-blocking rotation rollover
bug: a collection could show plenty of "Available" watch items in /list
while /start_vote saw far fewer, because the rest were on Rotation
Cooldown in a rotation nowhere near exhausted (its remaining pending
items weren't enough to satisfy the requested vote size, so it was never
rolled over automatically).

These tests exercise the real Discord-command entry points
(handle_start_vote_use_defaults) end to end -- vote creation, rotation
rollover, and confirmation-post embed synchronization together -- rather
than any single service in isolation.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import handle_start_vote_use_defaults
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionDatabaseConfiguration,
    SuggestionRulesConfig,
)
from watch_party_manager.domain.watch_item import WatchItemStatus
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.eligible_pool_warning_service import EligiblePoolWarningService
from watch_party_manager.persistence.eligible_pool_warning_state_repository import (
    JsonEligiblePoolWarningStateRepository,
)
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService


class FakeGuildConfigurationRepository:
    """Always reports "no guild configuration saved" -- the Eligible Pool
    Warning then has no Admin/Home Channel to resolve, so it never
    fires; these tests are about rollover mechanics, not the warning
    itself (see test_eligible_pool_warning_service.py)."""

    def get(self, guild_id: int):
        return None


GUILD_ID = 100
CHANNEL_ID = 200
WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int, roles=()) -> None:
        self.id = user_id
        self.roles = list(roles)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_embed = None
        self.sent_ephemeral = None

    async def send_message(self, content=None, ephemeral=False, view=None, embed=None) -> None:
        self.sent_message = content
        self.sent_embed = embed
        self.sent_ephemeral = ephemeral


class FakeSentMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeFollowup:
    def __init__(self) -> None:
        self.sent_messages: list[tuple] = []

    async def send(self, content=None, *, ephemeral=False, view=None) -> None:
        self.sent_messages.append((content, ephemeral, view))


class FakeInteraction:
    def __init__(self, user_id: int, guild_id=GUILD_ID, channel_id=CHANNEL_ID) -> None:
        self.user = FakeMember(user_id, roles=[FakeRole(WASH_CREW_ROLE_ID)])
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self._original_response = FakeSentMessage(message_id=9999)

    async def original_response(self):
        return self._original_response


class FakeConfirmationMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.edited_embed = None
        self.edit_calls = 0

    async def edit(self, *, embed=None, view=None) -> None:
        self.edited_embed = embed
        self.edit_calls += 1


class FakeConfirmationChannel:
    def __init__(self, *messages: FakeConfirmationMessage) -> None:
        self._messages = {message.id: message for message in messages}

    async def fetch_message(self, message_id):
        return self._messages[message_id]


class FakeBot:
    """The subset of WatchPartyBot sync_suggestion_status_embed (and now
    maybe_send_eligible_pool_warning) needs."""

    def __init__(
        self, suggestion_service, rotation_service, channel, configuration_repository=None, vote_service=None
    ) -> None:
        self.suggestion_service = suggestion_service
        self.suggestion_database_configuration_repository = configuration_repository
        self.rotation_service = rotation_service
        self.vote_service = vote_service
        self.permission_service = None
        self.guild_configuration_repository = FakeGuildConfigurationRepository()
        self.collection_eligibility_service = CollectionEligibilityService(suggestion_service, vote_service)
        self.eligible_pool_warning_service = EligiblePoolWarningService(
            self.collection_eligibility_service,
            JsonEligiblePoolWarningStateRepository(Path(tempfile.mkdtemp()) / "eligible_pool_warning_state.json"),
            self.guild_configuration_repository,
            configuration_repository,
            suggestion_service,
        )
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_channel(self, channel_id):
        return self._channel


class RotationRolloverEndToEndTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        self.nominee_selection_service = NomineeSelectionService(self.suggestion_service, self.vote_service)
        self.rotation_service = RotationService(
            self.suggestion_service, repository=JsonRotationRepository(root / "rotations.json")
        )
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=self.database.database_id,
                display_name="Movie Night",
                suggestion_rules=SuggestionRulesConfig(candidate_selection=CandidateSelectionMode.ROTATION_POOL),
            )
        )
        self.items = {}
        for title in ("Alien", "The Matrix", "Brazil"):
            watch_item = self.suggestion_service.suggest(title, database_id=self.database.database_id).watch_item
            self.items[title] = watch_item

    async def _start_vote(self, *, nominee_count: int, bot=None):
        interaction = FakeInteraction(user_id=1)
        await handle_start_vote_use_defaults(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=nominee_count,
            rotation_service=self.rotation_service,
            suggestion_database_configuration_repository=self.configuration_repository,
            bot=bot,
        )
        return interaction

    async def test_rollover_recovers_previously_presented_candidates_for_a_new_round(self) -> None:
        # Rotation-removal Phase 2: a suggestion presented in a closed
        # round never displays anything other than Available (there is no
        # Rotation Cooldown status left to refresh it away from) -- so
        # this test now only confirms the rollover *mechanism* itself
        # still recovers enough real candidates for the second round to
        # succeed, and that only the second round's own newly-nominated
        # candidates (via sync_vote_start_status_embeds) get their posts
        # edited, never anything the rollover alone touched.
        first_interaction = await self._start_vote(nominee_count=2)
        self.assertFalse(first_interaction.response.sent_ephemeral)
        first_round = self.vote_service.get_open_round()
        presented_ids = set(first_round.candidate_suggestion_ids)
        first_rotation = self.rotation_service.get_open_rotation(self.database.database_id)
        self.vote_service.close_round(first_round.id)

        # Attach a confirmation post reference to every candidate, exactly
        # as bot.py's finish_add_or_reactivate/vote flow already does in
        # production, so the sync call has something to edit.
        messages = {}
        for index, item in enumerate(self.items.values()):
            message_id = 1000 + index
            self.suggestion_service.set_confirmation_post_reference(
                item.id, GUILD_ID, CHANNEL_ID, message_id
            )
            messages[item.id] = FakeConfirmationMessage(message_id=message_id)
        channel = FakeConfirmationChannel(*messages.values())
        bot = FakeBot(self.suggestion_service, self.rotation_service, channel, self.configuration_repository, self.vote_service)

        second_interaction = await self._start_vote(nominee_count=2, bot=bot)

        self.assertFalse(second_interaction.response.sent_ephemeral, msg=second_interaction.response.sent_message)
        second_round = self.vote_service.get_open_round()
        self.assertEqual(len(second_round.candidate_suggestion_ids), 2)
        second_rotation = self.rotation_service.get_open_rotation(self.database.database_id)
        self.assertNotEqual(first_rotation.id, second_rotation.id)

        second_round_candidate_ids = set(second_round.candidate_suggestion_ids)
        for item in self.items.values():
            message = messages[item.id]
            if item.id in second_round_candidate_ids:
                self.assertEqual(1, message.edit_calls)
                status_field = next(field for field in message.edited_embed.fields if field.name == "Status")
                self.assertEqual("🗳️ In an Active Vote", status_field.value)
            else:
                # Never presented, or presented only in the first
                # (bot=None) round -- either way, nothing here should
                # have touched its post.
                self.assertEqual(0, message.edit_calls)

    async def test_no_rollover_needed_when_the_current_rotation_already_satisfies_the_request(self) -> None:
        messages = {
            item.id: FakeConfirmationMessage(message_id=1000 + index)
            for index, item in enumerate(self.items.values())
        }
        for item in self.items.values():
            self.suggestion_service.set_confirmation_post_reference(
                item.id, GUILD_ID, CHANNEL_ID, messages[item.id].id
            )
        channel = FakeConfirmationChannel(*messages.values())
        bot = FakeBot(self.suggestion_service, self.rotation_service, channel, self.configuration_repository, self.vote_service)

        interaction = await self._start_vote(nominee_count=2, bot=bot)

        self.assertFalse(interaction.response.sent_ephemeral)
        # Nothing was ever on cooldown yet, so the rollover mechanism
        # itself must not have touched anything -- but Suggestion Status
        # Synchronization now syncs every newly-presented candidate's own
        # post to Rotation Cooldown the moment the round opens (see
        # sync_vote_start_status_embeds), so exactly the 2 presented
        # candidates (out of 3 suggestions, with nominee_count=2) get
        # exactly one edit each; the one not presented gets none.
        self.assertEqual(2, sum(message.edit_calls for message in messages.values()))
        for message in messages.values():
            self.assertIn(message.edit_calls, (0, 1))

    async def test_missing_bot_still_rolls_over_but_skips_embed_sync(self) -> None:
        await self._start_vote(nominee_count=2)
        first_round = self.vote_service.get_open_round()
        self.vote_service.close_round(first_round.id)

        # No `bot` passed this time -- must not raise, and the vote
        # itself must still succeed via rollover.
        second_interaction = await self._start_vote(nominee_count=2, bot=None)

        self.assertFalse(second_interaction.response.sent_ephemeral, msg=second_interaction.response.sent_message)

    async def test_vote_is_blocked_when_rollover_still_leaves_too_few_eligible_items(self) -> None:
        # Vote Creation Validation: rollover must be tried before
        # reporting failure (see the other tests in this file), but a
        # collection genuinely too small for the requested candidate
        # count must still be blocked afterward, not silently rounded
        # down. "Brazil" is retired so the collection can never supply
        # more than 2 eligible watch items, no matter how many times its
        # rotation rolls over.
        self.suggestion_service.set_suggestion_status(self.items["Brazil"].id, WatchItemStatus.ARCHIVED)
        first_interaction = await self._start_vote(nominee_count=2)
        self.assertFalse(first_interaction.response.sent_ephemeral, msg=first_interaction.response.sent_message)
        first_round = self.vote_service.get_open_round()
        self.vote_service.close_round(first_round.id)

        # Both remaining eligible items are now on Rotation Cooldown from
        # the closed round, so this request must trigger a rollover --
        # but even a fresh rotation can only ever offer 2, never the 3
        # requested here.
        second_interaction = await self._start_vote(nominee_count=3)

        self.assertTrue(second_interaction.response.sent_ephemeral)
        self.assertIn("requires 3 candidates", second_interaction.response.sent_message)
        self.assertIn("only 2 are currently available", second_interaction.response.sent_message)
        self.assertIn("even after automatically starting a new rotation", second_interaction.response.sent_message)
        self.assertIsNone(self.vote_service.get_open_round())


if __name__ == "__main__":
    unittest.main()
