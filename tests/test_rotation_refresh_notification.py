"""Tests for the Rotation Refresh Notification (UI Polish: Rotation &
Collection Health) -- resolve_rotation_number and
build_rotation_refresh_notification, plus their end-to-end wiring into
/vote start and /list whenever an automatic rollover actually occurs.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import (
    build_rotation_refresh_notification,
    handle_list_suggestions,
    handle_start_vote_use_defaults,
    resolve_rotation_number,
)
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.rotation_low_pool_notification_service import RotationLowPoolNotificationService
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService

GUILD_ID = 100
CHANNEL_ID = 200
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555


class BuildRotationRefreshNotificationTests(unittest.TestCase):
    def test_wording_is_informational_not_warning_toned(self) -> None:
        message = build_rotation_refresh_notification(4)

        self.assertEqual("All eligible watch items have now been presented.\n\nStarting Rotation 4.", message)
        self.assertNotIn("⚠", message)
        self.assertNotIn("Warning", message)

    def test_includes_the_given_rotation_number(self) -> None:
        self.assertIn("Rotation 1.", build_rotation_refresh_notification(1))
        self.assertIn("Rotation 12.", build_rotation_refresh_notification(12))


class ResolveRotationNumberTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.rotation_service = RotationService(
            self.suggestion_service, repository=JsonRotationRepository(root / "rotations.json")
        )
        self.database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Alien", database_id=self.database.database_id)

    def test_first_rotation_is_numbered_one(self) -> None:
        rotation = self.rotation_service.get_or_start_rotation(self.database.database_id)

        number = resolve_rotation_number(self.rotation_service, self.database.database_id, rotation)

        self.assertEqual(1, number)

    def test_second_rotation_is_numbered_two(self) -> None:
        self.rotation_service.get_or_start_rotation(self.database.database_id)
        second_rotation = self.rotation_service.begin_next_rotation(self.database.database_id)

        number = resolve_rotation_number(self.rotation_service, self.database.database_id, second_rotation)

        self.assertEqual(2, number)

    def test_numbering_is_scoped_per_collection_not_by_the_global_rotation_id(self) -> None:
        # A rotation started for a different collection first must not
        # shift this collection's own numbering -- Rotation.id is a
        # single counter shared across every collection in the server.
        other_database = self.suggestion_service.create_database(
            "TV Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1
        ).database
        self.suggestion_service.suggest("Breaking Bad", database_id=other_database.database_id)
        self.rotation_service.get_or_start_rotation(other_database.database_id)
        self.rotation_service.begin_next_rotation(other_database.database_id)

        rotation = self.rotation_service.get_or_start_rotation(self.database.database_id)

        number = resolve_rotation_number(self.rotation_service, self.database.database_id, rotation)

        self.assertEqual(1, number)
        self.assertGreater(rotation.id, 2)  # the global id is well past 1, confirming numbering isn't just id


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int = 1, roles=(WASH_CREW_ROLE_ID,)) -> None:
        self.id = user_id
        self.roles = [FakeRole(role_id) for role_id in roles]


class FakeFollowup:
    def __init__(self) -> None:
        self.sent_messages: list[tuple] = []

    async def send(self, content=None, *, ephemeral=False, view=None) -> None:
        self.sent_messages.append((content, ephemeral, view))


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_embed = None
        self.sent_ephemeral = None
        self.sent_view = None
        self.sent_suppress_embeds = None

    async def send_message(self, content=None, ephemeral=False, view=None, embed=None, suppress_embeds=False) -> None:
        self.sent_message = content
        self.sent_embed = embed
        self.sent_ephemeral = ephemeral
        self.sent_view = view
        self.sent_suppress_embeds = suppress_embeds


class FakeSentMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeInteraction:
    def __init__(self, guild_id=GUILD_ID, channel_id=CHANNEL_ID, user=None) -> None:
        self.user = user or FakeMember()
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.guild = None
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self._original_response = FakeSentMessage(message_id=9999)

    async def original_response(self):
        return self._original_response


class FakeGuildConfigurationRepository:
    def get(self, guild_id: int):
        return None


class EndToEndNotificationTestCase(unittest.IsolatedAsyncioTestCase):
    """Shared fixture: a Balanced Random collection wired up like the
    real bot, so a genuine automatic rollover can be triggered and its
    Rotation Refresh Notification observed end to end.
    """

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
        self.eligibility_service = CollectionEligibilityService(self.suggestion_service, self.rotation_service)

        class FakeBot:
            def __init__(inner_self) -> None:
                inner_self.suggestion_service = self.suggestion_service
                inner_self.rotation_service = self.rotation_service
                inner_self.permission_service = PermissionService(
                    watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID
                )
                inner_self.wash_crew_role_id = WASH_CREW_ROLE_ID
                inner_self.suggestion_database_configuration_repository = self.configuration_repository
                inner_self.guild_configuration_repository = FakeGuildConfigurationRepository()
                inner_self.collection_eligibility_service = self.eligibility_service
                inner_self.rotation_low_pool_notification_service = RotationLowPoolNotificationService(
                    self.eligibility_service,
                    self.rotation_service,
                    inner_self.guild_configuration_repository,
                    self.configuration_repository,
                    self.suggestion_service,
                )

            def get_channel(self, channel_id):
                return None

            async def fetch_channel(self, channel_id):
                return None

        self.bot = FakeBot()

    def _add_items(self, count: int) -> list:
        return [
            self.suggestion_service.suggest(f"Title {index}", database_id=self.database.database_id).watch_item
            for index in range(count)
        ]


class VoteStartRotationRefreshNotificationTests(EndToEndNotificationTestCase):
    async def test_a_rollover_triggering_vote_start_sends_a_followup_notification(self) -> None:
        self._add_items(3)
        first_interaction = FakeInteraction()
        await handle_start_vote_use_defaults(
            first_interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=2,
            rotation_service=self.rotation_service,
            suggestion_database_configuration_repository=self.configuration_repository,
            bot=self.bot,
        )
        self.vote_service.close_round(self.vote_service.get_open_round().id)

        # Only 1 of 3 remains pending -- fewer than the default candidate
        # count of 2 -- so this second call must roll the rotation over.
        second_interaction = FakeInteraction()
        await handle_start_vote_use_defaults(
            second_interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=2,
            rotation_service=self.rotation_service,
            suggestion_database_configuration_repository=self.configuration_repository,
            bot=self.bot,
        )

        self.assertEqual(1, len(second_interaction.followup.sent_messages))
        content, ephemeral, _ = second_interaction.followup.sent_messages[0]
        self.assertIn("All eligible watch items have now been presented.", content)
        self.assertIn("Starting Rotation 2.", content)
        self.assertTrue(ephemeral)

    async def test_no_rollover_sends_no_followup_notification(self) -> None:
        self._add_items(3)
        interaction = FakeInteraction()

        await handle_start_vote_use_defaults(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=2,
            rotation_service=self.rotation_service,
            suggestion_database_configuration_repository=self.configuration_repository,
            bot=self.bot,
        )

        self.assertEqual([], interaction.followup.sent_messages)

    async def test_the_public_voting_post_itself_never_mentions_the_rollover(self) -> None:
        # Avoid clutter: every voter sees the post, so the notification
        # belongs only in the crew member's own ephemeral follow-up.
        self._add_items(3)
        first_interaction = FakeInteraction()
        await handle_start_vote_use_defaults(
            first_interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=2,
            rotation_service=self.rotation_service,
            suggestion_database_configuration_repository=self.configuration_repository,
            bot=self.bot,
        )
        self.vote_service.close_round(self.vote_service.get_open_round().id)

        second_interaction = FakeInteraction()
        await handle_start_vote_use_defaults(
            second_interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=2,
            rotation_service=self.rotation_service,
            suggestion_database_configuration_repository=self.configuration_repository,
            bot=self.bot,
        )

        self.assertIsNotNone(second_interaction.response.sent_embed)
        embed_text = str(second_interaction.response.sent_embed.to_dict())
        self.assertNotIn("Starting Rotation", embed_text)


class ListRotationRefreshNotificationTests(EndToEndNotificationTestCase):
    async def test_a_rollover_triggering_list_prepends_the_notification(self) -> None:
        items = self._add_items(3)
        self.rotation_service.get_or_start_rotation(self.database.database_id)
        self.rotation_service.record_presentation(self.database.database_id, [item.id for item in items[:2]])
        # 1 of 3 remains pending -- fewer than the default candidate
        # count of 3 -- so /list's own resolve() must roll over too.
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "eligible", False)

        message = interaction.response.sent_message
        self.assertIn("All eligible watch items have now been presented.", message)
        self.assertIn("Starting Rotation 2.", message)

    async def test_no_rollover_shows_no_notification(self) -> None:
        self._add_items(3)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "eligible", False)

        self.assertNotIn("Starting Rotation", interaction.response.sent_message)

    async def test_a_terminal_status_only_filter_never_shows_the_notification(self) -> None:
        # Vote Winners/Retired use peek(), which never rolls over --
        # there is nothing to announce when checking either of them alone.
        self._add_items(3)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "retired", False)

        self.assertNotIn("Starting Rotation", interaction.response.sent_message)


if __name__ == "__main__":
    unittest.main()
