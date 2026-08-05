import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    VotingGroup,
    build_customize_vote_modal_defaults,
    handle_customize_vote_submit,
    handle_start_vote_use_defaults,
    parse_optional_bool_field,
    parse_optional_int_field,
    parse_start_vote_overrides,
    parse_vote_reminder_minutes_before_close,
    resolve_customize_vote_default_candidate_selection,
    resolve_customize_vote_default_visibility,
)
from watch_party_manager.domain.guild_configuration import (
    GuildConfiguration,
    GuildVoteVisibility,
    NotificationsConfig,
    VoteNotificationsConfig,
    VotingDefaultsConfig,
)
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionDatabaseConfiguration,
    SuggestionRulesConfig,
)
from watch_party_manager.domain.vote import VoteVisibility
from watch_party_manager.filter_menu_view import FILTER_CATEGORY_GENRE, FILTER_CATEGORY_MEMBER
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.config_service import ConfigService
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.suggestion_service import SuggestionService
import watch_party_manager.bot as bot_module
from watch_party_manager.services.vote_service import VoteService
from watch_party_manager.setup_wizard_view import CandidateSelectionSelectComponent, VisibilitySelectComponent
from watch_party_manager.start_vote_view import (
    START_VOTE_CHOICE_TIMEOUT_SECONDS,
    CustomizeVoteModal,
    CustomizeVoteOverridesView,
    InsufficientFilteredPoolView,
    StartVoteChoiceView,
)

WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int, roles=(), display_name: str = None) -> None:
        self.id = user_id
        self.roles = list(roles)
        self.display_name = display_name if display_name is not None else f"User {user_id}"


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_embed = None
        self.sent_ephemeral = None
        self.sent_view = None
        self.sent_modal = None
        self.edited_content = None
        self.edited_view = None

    async def send_message(self, content=None, ephemeral=False, view=None, embed=None) -> None:
        self.sent_message = content
        self.sent_embed = embed
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal

    async def edit_message(self, content=None, view=None) -> None:
        self.edited_content = content
        self.edited_view = view


class FakeSentMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeInteraction:
    def __init__(self, user_id: int, guild_id=100, channel_id=200) -> None:
        self.user = FakeMember(user_id, roles=[FakeRole(WASH_CREW_ROLE_ID)])
        self.guild_id = guild_id
        self.guild = None
        self.channel_id = channel_id
        self.response = FakeResponse()
        self._original_response = FakeSentMessage(message_id=9999)

    async def original_response(self):
        return self._original_response


class StartVoteFlowTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(Path(self._temp_dir.name) / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self._temp_dir.name) / "suggestion_databases.json"
            ),
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(Path(self._temp_dir.name) / "voting.json")
        )
        self.nominee_selection_service = NomineeSelectionService(self.suggestion_service, self.vote_service)
        self.database_id = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=100, channel_id=200
        ).database.database_id
        for title in ("The Matrix", "Inception", "Interstellar", "Arrival", "Her"):
            self.suggestion_service.suggest(title, database_id=self.database_id)
        self.default_nominee_count = 3

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _interaction(self) -> FakeInteraction:
        return FakeInteraction(user_id=1)

    async def _confirm_customize_vote(self, review_interaction: FakeInteraction) -> FakeInteraction:
        """Click "Start Vote" on the Custom Vote Summary & Announcement
        review screen a handle_customize_vote_submit call produced --
        the round is only actually created once this fires (see
        bot.handle_customize_vote_submit's on_confirm closure), mirroring
        how each Discord component click is its own separate interaction.
        """
        view = review_interaction.response.sent_view
        confirm_button = next(child for child in view.children if child.custom_id == "wpm_edit_vote_confirm")
        confirm_interaction = self._interaction()
        await confirm_button.callback(confirm_interaction)
        return confirm_interaction


class UseDefaultsTests(StartVoteFlowTestCase):
    async def test_use_defaults_creates_a_round_with_configured_values(self) -> None:
        interaction = self._interaction()

        await handle_start_vote_use_defaults(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
        )

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round)
        self.assertEqual(vote_round.visibility, VoteVisibility.VISIBLE)
        self.assertEqual(len(vote_round.candidate_suggestion_ids), self.default_nominee_count)

    async def test_use_defaults_uses_the_configured_duration(self) -> None:
        before = datetime.now(timezone.utc)
        interaction = self._interaction()

        await handle_start_vote_use_defaults(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
        )

        vote_round = self.vote_service.get_open_round()
        expected = before + timedelta(hours=24)  # DEFAULT_VOTE_DURATION_MINUTES
        self.assertAlmostEqual(vote_round.closes_at.timestamp(), expected.timestamp(), delta=5)

    async def test_use_defaults_sends_the_interactive_voting_post(self) -> None:
        interaction = self._interaction()

        await handle_start_vote_use_defaults(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
        )

        self.assertFalse(interaction.response.sent_ephemeral)
        self.assertIsNotNone(interaction.response.sent_embed)
        self.assertIn("Is Open", interaction.response.sent_embed.title)
        self.assertIn("Voting ends:", interaction.response.sent_embed.description)

    async def test_use_defaults_still_enforces_wash_crew_permission(self) -> None:
        interaction = FakeInteraction(user_id=1)
        interaction.user = FakeMember(user_id=1, roles=[FakeRole(1)])  # not WASH Crew

        await handle_start_vote_use_defaults(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_use_defaults_still_rejects_insufficient_suggestions(self) -> None:
        empty_suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(Path(self._temp_dir.name) / "empty_suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self._temp_dir.name) / "empty_suggestion_databases.json"
            ),
        )
        empty_vote_service = VoteService(
            empty_suggestion_service, repository=JsonVoteRepository(Path(self._temp_dir.name) / "empty_voting.json")
        )
        empty_database_id = empty_suggestion_service.create_database(
            "Sunday Watch Party", guild_id=100, channel_id=200
        ).database.database_id
        empty_suggestion_service.suggest("Only One", database_id=empty_database_id)
        selector = NomineeSelectionService(empty_suggestion_service, empty_vote_service)
        interaction = self._interaction()

        await handle_start_vote_use_defaults(
            interaction,
            empty_vote_service,
            empty_suggestion_service,
            selector,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        # No nominee_count was given, so the message reports the
        # configured default (3), not a hardcoded floor.
        self.assertIn("requires 3 candidates", interaction.response.sent_message)
        self.assertIn("only 1 is currently available", interaction.response.sent_message)
        self.assertIsNone(empty_vote_service.get_open_round())


class CustomizeVoteTests(StartVoteFlowTestCase):
    async def test_customized_nominee_count(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="5",
            duration_text=None,
        )
        await self._confirm_customize_vote(interaction)

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round)
        self.assertEqual(len(vote_round.candidate_suggestion_ids), 5)

    async def test_customized_duration(self) -> None:
        before = datetime.now(timezone.utc)
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text=None,
            duration_text="3d",
        )
        await self._confirm_customize_vote(interaction)

        vote_round = self.vote_service.get_open_round()
        expected = before + timedelta(days=3)
        self.assertAlmostEqual(vote_round.closes_at.timestamp(), expected.timestamp(), delta=5)

    async def test_customized_visibility(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text=None,
            duration_text=None,
            visibility_override=GuildVoteVisibility.BLIND,
        )
        await self._confirm_customize_vote(interaction)

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.visibility, VoteVisibility.BLIND)

    async def test_blank_fields_fall_back_to_defaults(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="",
            duration_text="   ",
        )
        await self._confirm_customize_vote(interaction)

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.visibility, VoteVisibility.VISIBLE)
        self.assertEqual(len(vote_round.candidate_suggestion_ids), self.default_nominee_count)

    async def test_invalid_nominee_count_is_rejected(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="99",
            duration_text=None,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("between 2 and 10", interaction.response.sent_message)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_non_numeric_nominee_count_is_rejected_with_a_clear_message(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="a lot",
            duration_text=None,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("not a whole number", interaction.response.sent_message)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_invalid_duration_is_rejected(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text=None,
            duration_text="0",
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_invalid_duration_above_maximum_is_rejected(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text=None,
            duration_text="45",
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_customize_still_enforces_wash_crew_permission(self) -> None:
        interaction = self._interaction()
        interaction.user = FakeMember(user_id=1, roles=[FakeRole(1)])  # not WASH Crew

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="5",
            duration_text=None,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_defaults_unchanged_after_a_customized_vote(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="5",
            duration_text="2d",
            visibility_override=GuildVoteVisibility.BLIND,
        )
        await self._confirm_customize_vote(interaction)
        first_round = self.vote_service.get_open_round()
        self.vote_service.close_round(first_round.id)

        # The configured default is passed in explicitly each time by
        # bot.py (from WatchPartyBot.default_nominee_count) -- nothing in
        # the customize path ever mutates it. A fresh "Use Defaults" call
        # afterward should still use the original default, not 5.
        self.assertEqual(self.default_nominee_count, 3)
        second_interaction = self._interaction()
        await handle_start_vote_use_defaults(
            second_interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
        )

        second_round = self.vote_service.get_open_round()
        self.assertEqual(second_round.visibility, VoteVisibility.VISIBLE)
        self.assertEqual(len(second_round.candidate_suggestion_ids), 3)


class StartCommandPermissionTimingTests(StartVoteFlowTestCase):
    """Pre-Phase 3 UX Polish: /vote start's WASH Crew permission check
    must happen before the "Use Defaults / Customize This Vote" choice UI
    is ever shown, matching show_repair_confirmation's pattern.
    Previously the only check lived deep inside perform_start_vote, so a
    non-Crew member could see (and click through) the full choice UI
    before ever being told they lack permission.
    """

    def setUp(self) -> None:
        super().setUp()

        class FakeBot:
            pass

        self.bot = FakeBot()
        self.bot.wash_crew_role_id = WASH_CREW_ROLE_ID
        self.bot.vote_service = self.vote_service
        self.bot.suggestion_service = self.suggestion_service
        self.bot.nominee_selection_service = self.nominee_selection_service
        self.bot.default_nominee_count = self.default_nominee_count
        self.bot.scheduler_host = None
        self.bot.guild_configuration_repository = None
        self.bot.suggestion_database_configuration_repository = None

    async def test_a_non_crew_member_is_rejected_before_the_choice_ui_is_shown(self) -> None:
        group = VotingGroup(self.bot)
        interaction = FakeInteraction(user_id=1)
        interaction.user = FakeMember(user_id=1, roles=[FakeRole(1)])  # not WASH Crew

        await group.start.callback(group, interaction)

        self.assertIsNone(interaction.response.sent_view)
        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_an_unconfigured_role_is_rejected_before_the_choice_ui_is_shown(self) -> None:
        self.bot.wash_crew_role_id = None
        group = VotingGroup(self.bot)
        interaction = FakeInteraction(user_id=1)

        await group.start.callback(group, interaction)

        self.assertIsNone(interaction.response.sent_view)
        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("WASH_CREW_ROLE_ID", interaction.response.sent_message)

    async def test_a_crew_member_still_sees_the_choice_ui(self) -> None:
        group = VotingGroup(self.bot)
        interaction = self._interaction()

        await group.start.callback(group, interaction)

        self.assertIsInstance(interaction.response.sent_view, StartVoteChoiceView)


class CandidateSelectionOverrideTests(StartVoteFlowTestCase):
    """UI Polish (Voting Configuration Improvements): Customize This Vote
    can override a collection's Candidate Selection Mode for one round
    only, without ever changing the collection's own saved setting.
    """

    def setUp(self) -> None:
        super().setUp()
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            Path(self._temp_dir.name) / "suggestion_database_configurations.json"
        )
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=100,
                database_id=self.database_id,
                display_name="Sunday Watch Party",
                suggestion_rules=SuggestionRulesConfig(
                    candidate_selection=CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
                ),
            )
        )
        # Records which mode perform_start_vote actually resolved and
        # passed to build_candidate_selection_strategy, without changing
        # its behavior -- the direct, robust way to prove an override
        # took effect (or correctly fell back), replacing the removed
        # RotationService side-channel this suite used to rely on.
        self.modes_used: list[CandidateSelectionMode] = []
        real_build_strategy = bot_module.build_candidate_selection_strategy

        def recording_build_strategy(mode, suggestion_source):
            self.modes_used.append(mode)
            return real_build_strategy(mode, suggestion_source)

        bot_module.build_candidate_selection_strategy = recording_build_strategy
        self.addCleanup(setattr, bot_module, "build_candidate_selection_strategy", real_build_strategy)

    async def test_override_bypasses_the_configured_mode(self) -> None:
        interaction = self._interaction()
        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="3",
            duration_text=None,
            suggestion_database_configuration_repository=self.configuration_repository,
            candidate_selection_override=CandidateSelectionMode.INFINITE_POOL,
        )
        await self._confirm_customize_vote(interaction)

        self.assertIsNotNone(self.vote_service.get_open_round())
        self.assertEqual(self.modes_used, [CandidateSelectionMode.INFINITE_POOL])

    async def test_override_never_persists_to_the_collections_own_configuration(self) -> None:
        await handle_customize_vote_submit(
            self._interaction(),
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="3",
            duration_text=None,
            suggestion_database_configuration_repository=self.configuration_repository,
            candidate_selection_override=CandidateSelectionMode.INFINITE_POOL,
        )

        saved = self.configuration_repository.get(100, self.database_id)
        self.assertEqual(saved.suggestion_rules.candidate_selection, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS)

    async def test_no_override_falls_back_to_the_configured_mode(self) -> None:
        interaction = self._interaction()
        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="3",
            duration_text=None,
            suggestion_database_configuration_repository=self.configuration_repository,
        )
        await self._confirm_customize_vote(interaction)

        self.assertIsNotNone(self.vote_service.get_open_round())
        # Confirms the (unset) override correctly fell back to the
        # collection's own configured mode rather than silently using
        # something else.
        self.assertEqual(self.modes_used, [CandidateSelectionMode.FAVOR_OLDER_ADDITIONS])

    async def test_override_still_blocks_when_eligible_items_are_insufficient(self) -> None:
        # Vote Creation Validation: a Candidate Selection Mode override
        # changes *how* nominees are chosen, never *how many* are
        # required -- validation must still compare against the actual
        # resolved candidate count even when an override is in effect.
        interaction = self._interaction()
        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="10",
            duration_text=None,
            suggestion_database_configuration_repository=self.configuration_repository,
            candidate_selection_override=CandidateSelectionMode.INFINITE_POOL,
        )
        confirm_interaction = await self._confirm_customize_vote(interaction)

        self.assertTrue(confirm_interaction.response.sent_ephemeral)
        self.assertIn("requires 10 candidates", confirm_interaction.response.sent_message)
        self.assertIn("only 5 are currently available", confirm_interaction.response.sent_message)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_use_defaults_never_applies_an_override(self) -> None:
        await handle_start_vote_use_defaults(
            self._interaction(),
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            suggestion_database_configuration_repository=self.configuration_repository,
        )

        self.assertIsNotNone(self.vote_service.get_open_round())
        self.assertEqual(self.modes_used, [CandidateSelectionMode.FAVOR_OLDER_ADDITIONS])


class ConfigChangeAffectsFutureVotesTests(StartVoteFlowTestCase):
    """Candidate Selection Mode editing (issue: "Candidate Selection Mode
    cannot be edited after setup"): proves the full real-world loop --
    changing a collection's mode through ConfigService.set_database_candidate_selection
    (the exact method /config's Candidate Selection screen calls) actually
    changes what a later, un-overridden "Use Defaults" vote start does,
    since both share the one live SuggestionDatabaseConfigurationRepository
    instance -- never a separate/cached copy.
    """

    def setUp(self) -> None:
        super().setUp()
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            Path(self._temp_dir.name) / "suggestion_database_configurations.json"
        )
        self.config_service = ConfigService(
            GuildConfigurationRepository(Path(self._temp_dir.name) / "guild_configurations.json"),
            self.suggestion_service,
            self.configuration_repository,
        )
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=100,
                database_id=self.database_id,
                display_name="Sunday Watch Party",
                suggestion_rules=SuggestionRulesConfig(
                    candidate_selection=CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
                ),
            )
        )
        self.modes_used: list[CandidateSelectionMode] = []
        real_build_strategy = bot_module.build_candidate_selection_strategy

        def recording_build_strategy(mode, suggestion_source):
            self.modes_used.append(mode)
            return real_build_strategy(mode, suggestion_source)

        bot_module.build_candidate_selection_strategy = recording_build_strategy
        self.addCleanup(setattr, bot_module, "build_candidate_selection_strategy", real_build_strategy)

    async def test_a_config_change_is_used_by_the_next_use_defaults_vote(self) -> None:
        result = self.config_service.set_database_candidate_selection(
            100, self.database_id, CandidateSelectionMode.INFINITE_POOL
        )
        self.assertTrue(result.success)

        await handle_start_vote_use_defaults(
            self._interaction(),
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            suggestion_database_configuration_repository=self.configuration_repository,
        )

        self.assertIsNotNone(self.vote_service.get_open_round())
        self.assertEqual(self.modes_used, [CandidateSelectionMode.INFINITE_POOL])

    async def test_a_second_config_change_back_is_also_honored(self) -> None:
        self.config_service.set_database_candidate_selection(
            100, self.database_id, CandidateSelectionMode.INFINITE_POOL
        )
        self.config_service.set_database_candidate_selection(
            100, self.database_id, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
        )

        await handle_start_vote_use_defaults(
            self._interaction(),
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            suggestion_database_configuration_repository=self.configuration_repository,
        )

        self.assertIsNotNone(self.vote_service.get_open_round())
        self.assertEqual(self.modes_used, [CandidateSelectionMode.FAVOR_OLDER_ADDITIONS])


class CandidateCountResolutionConsistencyTests(StartVoteFlowTestCase):
    """Vote Creation Validation: the same resolved candidate count must be
    used both to check eligibility and to actually select nominees --
    never a separate, lower minimum for one than the other.
    """

    def setUp(self) -> None:
        super().setUp()
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            Path(self._temp_dir.name) / "suggestion_database_configurations.json"
        )
        self.eligibility_requested_counts: list[int] = []
        self.selection_counts: list[int] = []
        real_eligible_candidate_count = self.nominee_selection_service.eligible_candidate_count
        real_select_nominees = self.nominee_selection_service.select_nominees

        def recording_eligible_candidate_count(database_id, strategy=None, *, requested_count=None):
            self.eligibility_requested_counts.append(requested_count)
            return real_eligible_candidate_count(database_id, strategy, requested_count=requested_count)

        def recording_select_nominees(database_id, count, rng=None, strategy=None):
            self.selection_counts.append(count)
            return real_select_nominees(database_id, count, rng, strategy=strategy)

        self.nominee_selection_service.eligible_candidate_count = recording_eligible_candidate_count
        self.nominee_selection_service.select_nominees = recording_select_nominees

    async def test_use_defaults_resolves_the_same_count_for_eligibility_and_selection(self) -> None:
        await handle_start_vote_use_defaults(
            self._interaction(),
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            suggestion_database_configuration_repository=self.configuration_repository,
        )

        self.assertEqual(self.eligibility_requested_counts, [self.default_nominee_count])
        self.assertEqual(self.selection_counts, [self.default_nominee_count])

    async def test_customized_vote_resolves_the_same_count_for_eligibility_and_selection(self) -> None:
        interaction = self._interaction()
        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="4",
            duration_text=None,
            suggestion_database_configuration_repository=self.configuration_repository,
        )
        await self._confirm_customize_vote(interaction)

        self.assertEqual(self.eligibility_requested_counts, [4])
        self.assertEqual(self.selection_counts, [4])


class ResolveCustomizeVoteDefaultCandidateSelectionTests(StartVoteFlowTestCase):
    class FakeBot:
        def __init__(self, suggestion_service, suggestion_database_configuration_repository) -> None:
            self.suggestion_service = suggestion_service
            self.suggestion_database_configuration_repository = suggestion_database_configuration_repository

    def setUp(self) -> None:
        super().setUp()
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            Path(self._temp_dir.name) / "suggestion_database_configurations.json"
        )
        self.bot = self.FakeBot(self.suggestion_service, self.configuration_repository)

    def test_resolves_the_channels_configured_mode(self) -> None:
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=100,
                database_id=self.database_id,
                display_name="Sunday Watch Party",
                suggestion_rules=SuggestionRulesConfig(candidate_selection=CandidateSelectionMode.FAVOR_OLDER_ADDITIONS),
            )
        )

        result = resolve_customize_vote_default_candidate_selection(self.bot, guild_id=100, channel_id=200)

        self.assertEqual(result, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS)

    def test_falls_back_to_favor_older_additions_when_the_channel_matches_no_collection(self) -> None:
        result = resolve_customize_vote_default_candidate_selection(self.bot, guild_id=100, channel_id=999999)

        self.assertEqual(result, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS)

    def test_falls_back_to_favor_older_additions_with_no_guild_id(self) -> None:
        result = resolve_customize_vote_default_candidate_selection(self.bot, guild_id=None, channel_id=200)

        self.assertEqual(result, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS)


class CustomizeVoteOverridesViewTests(unittest.IsolatedAsyncioTestCase):
    async def _noop(self, interaction, mode, visibility) -> None:
        pass

    async def test_has_two_selects_and_a_continue_button(self) -> None:
        view = CustomizeVoteOverridesView(
            self._noop,
            default_candidate_selection=CandidateSelectionMode.FAVOR_NEW_ADDITIONS,
            default_visibility=GuildVoteVisibility.VISIBLE,
        )
        self.assertEqual(len(view.children), 3)
        self.assertEqual(view.children[2].label, "Continue to Vote Settings")

    async def test_continue_button_is_exposed_and_enabled_by_default(self) -> None:
        view = CustomizeVoteOverridesView(
            self._noop,
            default_candidate_selection=CandidateSelectionMode.FAVOR_NEW_ADDITIONS,
            default_visibility=GuildVoteVisibility.VISIBLE,
        )
        self.assertIs(view.continue_button, view.children[2])
        self.assertFalse(view.continue_button.disabled)

    async def test_continue_button_forwards_the_selected_mode_and_visibility(self) -> None:
        received = []

        async def on_continue(interaction, mode, visibility) -> None:
            received.append((mode, visibility))

        view = CustomizeVoteOverridesView(
            on_continue,
            default_candidate_selection=CandidateSelectionMode.FAVOR_OLDER_ADDITIONS,
            default_visibility=GuildVoteVisibility.BLIND,
        )
        await view.children[2].callback(interaction=object())

        # Nothing was ever selected in either dropdown, so each
        # preselected default is what gets forwarded -- matching
        # CandidateSelectionSelectComponent/VisibilitySelectComponent's
        # own documented "never touched" fallback.
        self.assertEqual(received, [(CandidateSelectionMode.FAVOR_OLDER_ADDITIONS, GuildVoteVisibility.BLIND)])


class CustomizeVoteReminderTests(StartVoteFlowTestCase):
    """FR-027: reminder overrides threaded through /start_vote's "Customize This Vote" flow."""

    async def _submit(self, reminder_enabled_text=None, reminder_minutes_text=None) -> FakeInteraction:
        interaction = self._interaction()
        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text=None,
            duration_text=None,
            reminder_enabled_text=reminder_enabled_text,
            reminder_minutes_text=reminder_minutes_text,
        )
        return interaction

    async def test_default_reminder_is_enabled_when_not_customized(self) -> None:
        # Using defaults (no reminder override) leaves reminder_enabled as
        # None on the round -- resolved later against the guild's default,
        # which is itself enabled by default (see VoteNotificationsConfig).
        interaction = await self._submit()
        await self._confirm_customize_vote(interaction)

        vote_round = self.vote_service.get_open_round()
        self.assertIsNone(vote_round.reminder_enabled)

    async def test_default_reminder_timing_is_not_overridden_when_not_customized(self) -> None:
        interaction = await self._submit()
        await self._confirm_customize_vote(interaction)

        vote_round = self.vote_service.get_open_round()
        self.assertIsNone(vote_round.reminder_minutes_before_close)

    async def test_custom_reminder_timing_is_stored_on_the_round(self) -> None:
        interaction = await self._submit(reminder_minutes_text="4h")
        await self._confirm_customize_vote(interaction)

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.reminder_minutes_before_close, 240)

    async def test_custom_reminder_timing_accepts_minutes(self) -> None:
        interaction = await self._submit(reminder_minutes_text="10m")
        await self._confirm_customize_vote(interaction)

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.reminder_minutes_before_close, 10)

    async def test_reminder_can_be_explicitly_disabled(self) -> None:
        interaction = await self._submit(reminder_enabled_text="no")
        await self._confirm_customize_vote(interaction)

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.reminder_enabled, False)

    async def test_reminder_can_be_explicitly_enabled(self) -> None:
        interaction = await self._submit(reminder_enabled_text="yes")
        await self._confirm_customize_vote(interaction)

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.reminder_enabled, True)

    async def test_invalid_reminder_minutes_is_rejected_and_creates_no_round(self) -> None:
        await self._submit(reminder_minutes_text="900h")

        self.assertIsNone(self.vote_service.get_open_round())

    async def test_invalid_reminder_enabled_text_is_rejected_and_creates_no_round(self) -> None:
        await self._submit(reminder_enabled_text="maybe")

        self.assertIsNone(self.vote_service.get_open_round())


class StartVoteChoiceViewTests(unittest.IsolatedAsyncioTestCase):
    async def _noop(self, interaction) -> None:
        pass

    async def test_choice_view_has_two_buttons(self) -> None:
        view = StartVoteChoiceView(self._noop, self._noop)
        self.assertEqual(len(view.children), 2)

    async def test_choice_view_uses_the_expected_timeout(self) -> None:
        view = StartVoteChoiceView(self._noop, self._noop)
        self.assertEqual(view.timeout, START_VOTE_CHOICE_TIMEOUT_SECONDS)

    async def test_choice_buttons_have_stable_labels_and_custom_ids(self) -> None:
        view = StartVoteChoiceView(self._noop, self._noop)
        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Use Defaults", "wpm_start_vote_use_defaults"),
                ("Customize This Vote", "wpm_start_vote_customize"),
            ],
        )

    async def test_use_defaults_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_use_defaults(interaction) -> None:
            calls.append("used_defaults")

        view = StartVoteChoiceView(on_use_defaults, self._noop)
        await view.children[0].callback(interaction=object())

        self.assertEqual(calls, ["used_defaults"])

    async def test_customize_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_customize(interaction) -> None:
            calls.append("customize")

        view = StartVoteChoiceView(self._noop, on_customize)
        await view.children[1].callback(interaction=object())

        self.assertEqual(calls, ["customize"])

    async def test_interaction_cancellation_creates_no_round(self) -> None:
        # Simply constructing the choice view (as /start_vote does) and
        # never invoking either callback -- as happens if the member never
        # clicks anything, or the view times out -- must never create a
        # round on its own.
        StartVoteChoiceView(self._noop, self._noop)
        # No assertion needed beyond "this doesn't raise and does
        # nothing" -- there's no vote_service in scope here at all,
        # which is exactly the point: nothing can be created without an
        # explicit choice.


class BuildCustomizeVoteModalDefaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.repository = GuildConfigurationRepository(Path(self._temp_dir.name) / "guild_configurations.json")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_falls_back_to_hardcoded_defaults_with_no_guild_configuration(self) -> None:
        result = build_customize_vote_modal_defaults(
            default_nominee_count=6, guild_id=100, guild_configuration_repository=self.repository
        )
        self.assertEqual(result["default_nominee_count_display"], "6")
        self.assertEqual(result["default_reminder_enabled_display"], "Yes")
        self.assertEqual(result["default_reminder_minutes_display"], "1 day")

    def test_reflects_the_guilds_saved_configuration(self) -> None:
        configuration = GuildConfiguration(
            guild_id=100,
            guild_name="Test Guild",
            voting_defaults=VotingDefaultsConfig(
                candidate_count=5, duration_minutes=48 * 60, visibility=GuildVoteVisibility.BLIND
            ),
            notifications=NotificationsConfig(
                vote=VoteNotificationsConfig(vote_ending_reminder=False, reminder_minutes_before_close=360)
            ),
        )
        self.repository.save(configuration)

        result = build_customize_vote_modal_defaults(
            default_nominee_count=6, guild_id=100, guild_configuration_repository=self.repository
        )

        self.assertEqual(result["default_duration_display"], "2 days")
        self.assertEqual(result["default_reminder_enabled_display"], "No")
        self.assertEqual(result["default_reminder_minutes_display"], "6 hours")

    def test_handles_no_guild_id(self) -> None:
        result = build_customize_vote_modal_defaults(
            default_nominee_count=6, guild_id=None, guild_configuration_repository=self.repository
        )
        self.assertEqual(result["default_nominee_count_display"], "6")


class ResolveCustomizeVoteDefaultVisibilityTests(unittest.TestCase):
    """General Fixed-Option Control Audit: visibility's own preselection
    resolver, mirroring resolve_customize_vote_default_candidate_selection
    -- visibility is guild-wide (not per-collection), so this only needs
    a guild_id, not a channel_id.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.repository = GuildConfigurationRepository(Path(self._temp_dir.name) / "guild_configurations.json")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_resolves_the_guilds_configured_visibility(self) -> None:
        self.repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Test Guild",
                voting_defaults=VotingDefaultsConfig(visibility=GuildVoteVisibility.BLIND),
            )
        )

        result = resolve_customize_vote_default_visibility(100, self.repository)

        self.assertEqual(result, GuildVoteVisibility.BLIND)

    def test_falls_back_to_visible_with_no_saved_configuration(self) -> None:
        result = resolve_customize_vote_default_visibility(100, self.repository)

        self.assertEqual(result, GuildVoteVisibility.VISIBLE)

    def test_falls_back_to_visible_with_no_guild_id(self) -> None:
        result = resolve_customize_vote_default_visibility(None, self.repository)

        self.assertEqual(result, GuildVoteVisibility.VISIBLE)

    def test_falls_back_to_visible_with_no_repository(self) -> None:
        result = resolve_customize_vote_default_visibility(100, None)

        self.assertEqual(result, GuildVoteVisibility.VISIBLE)


class CustomizeVoteModalTests(unittest.TestCase):
    async def _noop(
        self, interaction, nominee_count_text, duration_text,
        reminder_enabled_text, reminder_minutes_text,
    ) -> None:
        pass

    def test_modal_has_four_fields(self) -> None:
        # Visibility moved out to its own dropdown on
        # CustomizeVoteOverridesView (General Fixed-Option Control
        # Audit) -- the modal now covers only the fields Discord can't
        # express as a Select: nominee count, duration, and the two
        # reminder fields.
        modal = CustomizeVoteModal(self._noop)
        self.assertEqual(len(modal.children), 4)

    def test_modal_has_no_visibility_field(self) -> None:
        modal = CustomizeVoteModal(self._noop)
        self.assertFalse(hasattr(modal, "visibility_input"))

    def test_modal_fields_are_all_optional(self) -> None:
        modal = CustomizeVoteModal(self._noop)
        self.assertTrue(all(not field.required for field in modal.children))

    def test_includes_reminder_fields(self) -> None:
        modal = CustomizeVoteModal(self._noop)
        self.assertIn(modal.reminder_enabled_input, modal.children)
        self.assertIn(modal.reminder_minutes_input, modal.children)

    def test_the_two_reminder_fields_have_distinct_labels(self) -> None:
        # UX Polish: both reminder fields previously read "Reminder before
        # close" (one with a "? (yes/no)" suffix), easy to misread when
        # scanning the modal quickly.
        modal = CustomizeVoteModal(self._noop)
        self.assertNotEqual(modal.reminder_enabled_input.label, modal.reminder_minutes_input.label)

    def test_placeholders_show_plain_wording_when_no_default_is_supplied(self) -> None:
        modal = CustomizeVoteModal(self._noop)
        self.assertEqual(modal.nominee_count_input.placeholder, "Leave blank to use the configured default")
        self.assertEqual(
            modal.reminder_enabled_input.placeholder, "Leave blank to use the configured default"
        )
        self.assertNotIn("(", modal.duration_input.placeholder)
        self.assertNotIn("(", modal.reminder_minutes_input.placeholder)

    def test_placeholders_name_the_actual_configured_value_when_supplied(self) -> None:
        modal = CustomizeVoteModal(
            self._noop,
            default_nominee_count_display="5",
            default_duration_display="12 hours",
            default_reminder_enabled_display="Yes",
            default_reminder_minutes_display="24 hours",
        )
        self.assertEqual(
            modal.nominee_count_input.placeholder, "Leave blank to use the configured default (5)"
        )
        self.assertIn("(12 hours)", modal.duration_input.placeholder)
        self.assertEqual(
            modal.reminder_enabled_input.placeholder, "Leave blank to use the configured default (Yes)"
        )
        self.assertIn("(24 hours)", modal.reminder_minutes_input.placeholder)


class CustomizeVoteModalSubmitTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_forwards_all_four_raw_values(self) -> None:
        received = []

        async def on_submit(interaction, nominee_count_text, duration_text,
                             reminder_enabled_text, reminder_minutes_text) -> None:
            received.append(
                (nominee_count_text, duration_text, reminder_enabled_text, reminder_minutes_text)
            )

        modal = CustomizeVoteModal(on_submit)
        modal.nominee_count_input._value = "5"
        modal.duration_input._value = "3d"
        modal.reminder_enabled_input._value = "no"
        modal.reminder_minutes_input._value = "12h"

        await modal.on_submit(interaction=object())

        self.assertEqual(received, [("5", "3d", "no", "12h")])



class ParseOptionalIntFieldTests(unittest.TestCase):
    def test_returns_none_for_none(self) -> None:
        self.assertIsNone(parse_optional_int_field(None))

    def test_returns_none_for_blank_string(self) -> None:
        self.assertIsNone(parse_optional_int_field("   "))

    def test_parses_a_valid_integer(self) -> None:
        self.assertEqual(parse_optional_int_field("5"), 5)

    def test_strips_whitespace(self) -> None:
        self.assertEqual(parse_optional_int_field("  7  "), 7)

    def test_rejects_non_numeric_text(self) -> None:
        with self.assertRaises(ValueError):
            parse_optional_int_field("abc")


class ParseStartVoteOverridesTests(unittest.TestCase):
    def test_blank_values_resolve_to_defaults(self) -> None:
        # Blank visibility resolves to None -- perform_start_vote (via
        # parse_vote_visibility) then applies the guild's configured
        # default rather than a hardcoded value (Release Polish Batch 2,
        # Priority 6).
        self.assertEqual(
            parse_start_vote_overrides(None, "   ", ""),
            (None, None, None, None, None),
        )

    def test_values_are_trimmed_and_parsed(self) -> None:
        self.assertEqual(
            parse_start_vote_overrides(" 5 ", " 3d ", " blind "),
            (5, 72 * 60, "blind", None, None),
        )

    def test_explicit_minute_unit_is_parsed(self) -> None:
        # Release Candidate Polish (Vote Duration): minute-level
        # precision is supported, not just whole-hour amounts.
        self.assertEqual(
            parse_start_vote_overrides(None, "10m", None),
            (None, 10, None, None, None),
        )

    def test_explicit_hour_unit_is_parsed(self) -> None:
        self.assertEqual(
            parse_start_vote_overrides(None, "4h", None),
            (None, 4 * 60, None, None, None),
        )

    def test_explicit_day_unit_is_parsed(self) -> None:
        self.assertEqual(
            parse_start_vote_overrides(None, "3 days", None),
            (None, 72 * 60, None, None, None),
        )

    def test_explicit_week_unit_is_parsed(self) -> None:
        # Requirement 3: weeks are part of the one standardized duration
        # syntax now, everywhere -- including vote duration.
        self.assertEqual(
            parse_start_vote_overrides(None, "1 week", None),
            (None, 168 * 60, None, None, None),
        )

    def test_numeric_parse_errors_are_preserved(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a whole number"):
            parse_start_vote_overrides("many", None, None)

    def test_bare_number_duration_is_rejected(self) -> None:
        # Requirement 3: the old "bare number means days" convenience is
        # deliberately no longer accepted -- an explicit unit is required.
        with self.assertRaisesRegex(ValueError, "whole number"):
            parse_start_vote_overrides(None, "3", None)

    def test_invalid_duration_unit_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "whole number"):
            parse_start_vote_overrides(None, "3 fortnights", None)

    # --- FR-027: reminder overrides -------------------------------------------

    def test_blank_reminder_fields_resolve_to_none(self) -> None:
        nominee_count, duration_minutes, visibility, reminder_enabled, reminder_minutes = parse_start_vote_overrides(
            None, None, None, "", "  "
        )
        self.assertIsNone(reminder_enabled)
        self.assertIsNone(reminder_minutes)

    def test_reminder_enabled_yes_is_parsed_true(self) -> None:
        *_, reminder_enabled, _ = parse_start_vote_overrides(None, None, None, "yes", None)
        self.assertTrue(reminder_enabled)

    def test_reminder_enabled_no_is_parsed_false(self) -> None:
        *_, reminder_enabled, _ = parse_start_vote_overrides(None, None, None, "no", None)
        self.assertFalse(reminder_enabled)

    def test_reminder_minutes_is_parsed_using_the_shared_duration_syntax(self) -> None:
        *_, reminder_minutes = parse_start_vote_overrides(None, None, None, None, "12h")
        self.assertEqual(reminder_minutes, 720)

    def test_invalid_reminder_enabled_text_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "yes' or 'no'"):
            parse_start_vote_overrides(None, None, None, "maybe", None)

    def test_invalid_reminder_minutes_text_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "whole number"):
            parse_start_vote_overrides(None, None, None, None, "soon")


class ParseOptionalBoolFieldTests(unittest.TestCase):
    def test_returns_none_for_none(self) -> None:
        self.assertIsNone(parse_optional_bool_field(None))

    def test_returns_none_for_blank_string(self) -> None:
        self.assertIsNone(parse_optional_bool_field("   "))

    def test_parses_yes_variants_as_true(self) -> None:
        for value in ["yes", "y", "true", "on", "enable", "enabled", "YES", " Yes "]:
            with self.subTest(value=value):
                self.assertTrue(parse_optional_bool_field(value))

    def test_parses_no_variants_as_false(self) -> None:
        for value in ["no", "n", "false", "off", "disable", "disabled", "NO", " No "]:
            with self.subTest(value=value):
                self.assertFalse(parse_optional_bool_field(value))

    def test_rejects_unrecognized_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "yes' or 'no'"):
            parse_optional_bool_field("maybe")


class ParseVoteReminderMinutesBeforeCloseTests(unittest.TestCase):
    def test_none_resolves_to_none(self) -> None:
        self.assertIsNone(parse_vote_reminder_minutes_before_close(None))

    def test_accepts_a_value_within_bounds(self) -> None:
        self.assertEqual(parse_vote_reminder_minutes_before_close(24 * 60), 24 * 60)

    def test_accepts_the_minimum_bound(self) -> None:
        self.assertEqual(parse_vote_reminder_minutes_before_close(1), 1)

    def test_accepts_the_maximum_bound(self) -> None:
        self.assertEqual(parse_vote_reminder_minutes_before_close(720 * 60), 720 * 60)

    def test_rejects_zero(self) -> None:
        with self.assertRaises(ValueError):
            parse_vote_reminder_minutes_before_close(0)

    def test_rejects_a_value_above_the_maximum(self) -> None:
        with self.assertRaises(ValueError):
            parse_vote_reminder_minutes_before_close(720 * 60 + 1)


class VisibilityOverrideTests(StartVoteFlowTestCase):
    """General Fixed-Option Control Audit: Vote Visibility can be
    overridden for one round only, mirroring CandidateSelectionOverrideTests
    exactly -- both overrides share the identical "this round only, never
    the stored default" contract.
    """

    def setUp(self) -> None:
        super().setUp()
        self.guild_configuration_repository = GuildConfigurationRepository(
            Path(self._temp_dir.name) / "guild_configurations.json"
        )
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Test Guild",
                voting_defaults=VotingDefaultsConfig(visibility=GuildVoteVisibility.VISIBLE),
            )
        )

    async def test_override_bypasses_the_guilds_configured_visibility(self) -> None:
        interaction = self._interaction()
        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="3",
            duration_text=None,
            guild_configuration_repository=self.guild_configuration_repository,
            visibility_override=GuildVoteVisibility.BLIND,
        )
        await self._confirm_customize_vote(interaction)

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round)
        self.assertEqual(vote_round.visibility, VoteVisibility.BLIND)

    async def test_override_never_persists_to_the_guilds_own_configuration(self) -> None:
        await handle_customize_vote_submit(
            self._interaction(),
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="3",
            duration_text=None,
            guild_configuration_repository=self.guild_configuration_repository,
            visibility_override=GuildVoteVisibility.BLIND,
        )

        saved = self.guild_configuration_repository.get(100)
        self.assertEqual(saved.voting_defaults.visibility, GuildVoteVisibility.VISIBLE)

    async def test_no_override_falls_back_to_the_guilds_configured_visibility(self) -> None:
        interaction = self._interaction()
        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="3",
            duration_text=None,
            guild_configuration_repository=self.guild_configuration_repository,
        )
        await self._confirm_customize_vote(interaction)

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.visibility, VoteVisibility.VISIBLE)

    async def test_use_defaults_never_applies_a_visibility_override(self) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Test Guild",
                voting_defaults=VotingDefaultsConfig(visibility=GuildVoteVisibility.BLIND),
            )
        )

        await handle_start_vote_use_defaults(
            self._interaction(),
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            guild_configuration_repository=self.guild_configuration_repository,
        )

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.visibility, VoteVisibility.BLIND)


class FakeSchedulerHostForCustomizeFlow:
    def __init__(self) -> None:
        self.scheduler_service = None


class FakeEligiblePoolWarningServiceForCustomizeFlow:
    """Never actually warns -- only exists so handle_start_vote_completion's
    post-creation Eligible Pool Warning check (unrelated to this feature)
    has something to call.
    """

    def evaluate(self, *, guild_id: int, database_id: int):
        from types import SimpleNamespace

        return SimpleNamespace(should_send=False, destination_channel_id=None, message="")


class CustomizeVoteFlowUiConsistencyTests(StartVoteFlowTestCase):
    """UI Review: the real /vote start -> Customize This Vote screen must
    only describe controls that are actually present. General
    Fixed-Option Control Audit added a real Vote Visibility dropdown
    alongside Candidate Selection Mode's existing one -- the old
    instructional text (which promised visibility only later, inside a
    free-text modal field) would now be stale if left unchanged.
    """

    def setUp(self) -> None:
        super().setUp()

        class FakeBot:
            pass

        self.bot = FakeBot()
        self.bot.vote_service = self.vote_service
        self.bot.suggestion_service = self.suggestion_service
        self.bot.nominee_selection_service = self.nominee_selection_service
        self.bot.wash_crew_role_id = WASH_CREW_ROLE_ID
        self.bot.default_nominee_count = self.default_nominee_count
        self.bot.scheduler_host = FakeSchedulerHostForCustomizeFlow()
        self.bot.guild_configuration_repository = GuildConfigurationRepository(
            Path(self._temp_dir.name) / "guild_configurations.json"
        )
        self.bot.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            Path(self._temp_dir.name) / "suggestion_database_configurations.json"
        )
        self.bot.eligible_pool_warning_service = FakeEligiblePoolWarningServiceForCustomizeFlow()

    async def _open_customize_screen(self):
        group = VotingGroup(self.bot)
        start_interaction = self._interaction()
        await group.start.callback(group, start_interaction)

        choice_view = start_interaction.response.sent_view
        customize_button = next(c for c in choice_view.children if c.label == "Customize This Vote")
        customize_interaction = self._interaction()
        await customize_button.callback(interaction=customize_interaction)
        return customize_interaction

    async def test_instructional_text_mentions_both_overridable_settings(self) -> None:
        interaction = await self._open_customize_screen()

        message = interaction.response.sent_message
        self.assertIn("Nominee Selection Mode", message)
        self.assertIn("Vote Visibility", message)

    async def test_instructional_text_does_not_claim_visibility_is_in_the_modal(self) -> None:
        interaction = await self._open_customize_screen()

        message = interaction.response.sent_message
        # The old text said "...the rest of this vote's settings
        # (candidate count, duration, and visibility)" -- visibility no
        # longer belongs to "the rest" (the modal); it's already been
        # chosen on this very screen.
        self.assertNotIn("duration, and visibility", message)

    async def test_screen_shows_both_dropdowns(self) -> None:
        interaction = await self._open_customize_screen()

        overrides_view = interaction.response.sent_view
        self.assertIsInstance(overrides_view, CustomizeVoteOverridesView)
        self.assertIsInstance(overrides_view.candidate_selection_select, CandidateSelectionSelectComponent)
        self.assertIsInstance(overrides_view.visibility_select, VisibilitySelectComponent)

    async def test_start_vote_button_is_present_on_the_overrides_screen(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        start_vote_button = next(
            child for child in overrides_view.children
            if getattr(child, "custom_id", None) == "wpm_start_vote_customize_start_now"
        )
        self.assertEqual(start_vote_button.label, "Start Vote")

    async def test_start_vote_button_creates_a_round_immediately_without_a_modal(self) -> None:
        # Custom Vote UX Polish: Start Vote must never require opening
        # Vote Settings' modal -- every unfilled field uses its
        # configured default, exactly like an untouched modal submission.
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        start_vote_button = next(
            child for child in overrides_view.children
            if getattr(child, "custom_id", None) == "wpm_start_vote_customize_start_now"
        )
        start_interaction = self._interaction()
        await start_vote_button.callback(interaction=start_interaction)

        # Lands on the Review This Vote confirmation screen (never a
        # modal) -- confirming there creates the round.
        self.assertIsNone(start_interaction.response.sent_modal)
        await self._confirm_customize_vote(start_interaction)
        self.assertIsNotNone(self.vote_service.get_open_round())


class FakeMembershipServiceForFilters:
    """Minimal stand-in for MembershipService -- only get_role_config() is
    ever called through bot.membership_service; is_current_member() is
    always the real MembershipService staticmethod (see
    bot.py's on_member_filter_changed).
    """

    def __init__(self, role_id: int) -> None:
        self._role_id = role_id

    def get_role_config(self, guild_id):
        from types import SimpleNamespace

        return SimpleNamespace(role_id=self._role_id)


class CustomVoteFilterUiFlowTests(CustomizeVoteFlowUiConsistencyTests):
    """Sections 3-8: the real /vote start -> Customize This Vote screen's
    shared FilterMenuView (Genre and Member here; IMDb Rating/MPAA
    Rating/Actor get their own coverage elsewhere), exercised end-to-end
    through the actual VotingGroup wiring (bot.py's on_customize closure)
    -- not just the underlying filter/service logic (see
    test_nominee_pool_filter.py for that) and not just the shared view
    components in isolation (see test_filter_menu_view.py for that).

    The overrides screen (CustomizeVoteOverridesView) no longer hosts the
    Member/Genre selects directly -- an "Edit Filters" button opens the
    shared filter_menu_view.FilterMenuView (identical to /random watch's,
    see bot.py's create_filter_menu_session). Editing a filter still
    lives on that filter's own small edit screen; Member's edit screen
    shows inline validation feedback and needs an explicit "Back to
    Filters" step, while Genre returns directly to a refreshed menu.
    "Back to Vote Settings" (the filter menu's secondary action) returns
    to the original overrides screen, re-syncing its own Continue
    button's disabled state (see bot.py's on_back_to_overrides) -- the
    filter menu's own primary button and overrides_view.continue_button
    are two different Button objects that must be independently checked.
    """

    WATCH_PARTY_FILTER_ROLE_ID = 777

    def setUp(self) -> None:
        super().setUp()
        self.bot.membership_service = FakeMembershipServiceForFilters(self.WATCH_PARTY_FILTER_ROLE_ID)
        # KC (111): 2 eligible Comedy suggestions. Someone else (222): 1
        # Horror suggestion. The base 5 suggestions from setUp have no
        # recorded submitter/genre at all, so they never match either
        # filter -- exactly the "legacy suggestion" case.
        self.suggestion_service.suggest(
            "Airplane!", database_id=self.database_id, original_suggester="111", genres=("Comedy",)
        )
        self.suggestion_service.suggest(
            "Ghostbusters", database_id=self.database_id, original_suggester="111", genres=("Comedy",)
        )
        self.suggestion_service.suggest(
            "The Shining", database_id=self.database_id, original_suggester="222", genres=("Horror",)
        )

    def _kc_member(self) -> FakeMember:
        return FakeMember(user_id=111, roles=[FakeRole(self.WATCH_PARTY_FILTER_ROLE_ID)], display_name="KC")

    # --- Shared FilterMenuView navigation helpers ---------------------

    def _edit_filters_button(self, overrides_view):
        return next(
            child for child in overrides_view.children
            if getattr(child, "custom_id", None) == "wpm_start_vote_customize_edit_filters"
        )

    async def _open_filter_menu(self, overrides_view):
        interaction = self._interaction()
        await self._edit_filters_button(overrides_view).callback(interaction=interaction)
        return interaction.response.edited_view

    def _category_select(self, menu_view):
        return next(
            child for child in menu_view.children
            if getattr(child, "custom_id", None) == "wpm_filter_menu_category"
        )

    async def _open_category_edit(self, menu_view, category: str):
        self._category_select(menu_view)._values = [category]
        interaction = self._interaction()
        await self._category_select(menu_view).callback(interaction=interaction)
        return interaction.response.edited_view

    def _member_select(self, member_edit_view):
        return next(
            child for child in member_edit_view.children
            if getattr(child, "custom_id", None) == "wpm_filter_menu_member"
        )

    def _genre_select(self, genre_edit_view):
        return next(
            child for child in genre_edit_view.children
            if getattr(child, "custom_id", None) == "wpm_filter_menu_genre"
        )

    def _back_to_menu_button(self, edit_view):
        return next(
            child for child in edit_view.children
            if getattr(child, "custom_id", None) == "wpm_filter_menu_back"
        )

    async def _back_to_menu(self, edit_view):
        interaction = self._interaction()
        await self._back_to_menu_button(edit_view).callback(interaction=interaction)
        return interaction.response.edited_view

    def _menu_continue_button(self, menu_view):
        return next(
            child for child in menu_view.children
            if getattr(child, "custom_id", None) == "wpm_start_vote_customize_filter_menu_continue"
        )

    def _menu_back_to_overrides_button(self, menu_view):
        return next(
            child for child in menu_view.children
            if getattr(child, "custom_id", None) == "wpm_filter_menu_secondary"
        )

    async def _back_to_overrides(self, menu_view):
        interaction = self._interaction()
        await self._menu_back_to_overrides_button(menu_view).callback(interaction=interaction)
        return interaction

    async def _select_member(self, menu_view, fake_member, *, guild=None):
        """Navigate Filters -> Member -> select fake_member. Returns the
        interaction so the caller can inspect edited_content (the inline
        validation status line) and edited_view (the still-open Member
        edit screen, one Back click away from a refreshed menu)."""
        member_edit_view = await self._open_category_edit(menu_view, FILTER_CATEGORY_MEMBER)
        self._member_select(member_edit_view)._values = [fake_member]
        interaction = self._interaction()
        if guild is not None:
            interaction.guild = guild
        await self._member_select(member_edit_view).callback(interaction=interaction)
        return interaction

    async def _clear_member(self, menu_view):
        member_edit_view = await self._open_category_edit(menu_view, FILTER_CATEGORY_MEMBER)
        self._member_select(member_edit_view)._values = []
        interaction = self._interaction()
        await self._member_select(member_edit_view).callback(interaction=interaction)
        return interaction

    async def _set_member_and_return_to_menu(self, menu_view, fake_member, *, guild=None):
        interaction = await self._select_member(menu_view, fake_member, guild=guild)
        return await self._back_to_menu(interaction.response.edited_view)

    async def _clear_member_and_return_to_menu(self, menu_view):
        interaction = await self._clear_member(menu_view)
        return await self._back_to_menu(interaction.response.edited_view)

    async def _select_genre(self, menu_view, genre: str):
        """Navigate Filters -> Genre -> select genre. Genre selection
        returns directly to a refreshed FilterMenuView (no Back step
        needed, unlike Member's inline validation feedback screen)."""
        genre_edit_view = await self._open_category_edit(menu_view, FILTER_CATEGORY_GENRE)
        self._genre_select(genre_edit_view)._values = [genre]
        interaction = self._interaction()
        await self._genre_select(genre_edit_view).callback(interaction=interaction)
        return interaction.response.edited_view

    async def test_screen_includes_member_and_genre_filter_selects(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        self._edit_filters_button(overrides_view)  # raises StopIteration if missing

        menu_view = await self._open_filter_menu(overrides_view)
        category_values = {option.value for option in self._category_select(menu_view).options}
        self.assertIn(FILTER_CATEGORY_MEMBER, category_values)
        self.assertIn(FILTER_CATEGORY_GENRE, category_values)

    async def test_selecting_a_valid_member_shows_their_eligible_count(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)

        select_interaction = await self._select_member(menu_view, self._kc_member())

        self.assertIn("KC has 2 eligible suggestions", select_interaction.response.edited_content)

    async def test_selecting_a_non_watch_party_member_is_rejected(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        non_member = FakeMember(user_id=333, roles=[], display_name="Stranger")

        select_interaction = await self._select_member(menu_view, non_member)

        self.assertIn(
            "is not the server owner, a WASH Crew member, or a current Watch Party member",
            select_interaction.response.edited_content,
        )

    async def test_selecting_a_member_with_no_eligible_suggestions_is_rejected(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        idle_member = FakeMember(
            user_id=999, roles=[FakeRole(self.WATCH_PARTY_FILTER_ROLE_ID)], display_name="Idle"
        )

        select_interaction = await self._select_member(menu_view, idle_member)

        self.assertIn("has no eligible suggestions", select_interaction.response.edited_content)

    async def test_genre_select_options_show_eligible_counts(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        genre_edit_view = await self._open_category_edit(menu_view, FILTER_CATEGORY_GENRE)
        genre_select = self._genre_select(genre_edit_view)

        options_by_label = {option.label: option for option in genre_select.options}
        self.assertIn("Comedy", options_by_label)
        self.assertEqual(options_by_label["Comedy"].description, "2 eligible suggestions")
        self.assertIn("Horror", options_by_label)
        self.assertEqual(options_by_label["Horror"].description, "1 eligible suggestion")

    async def _complete_customize_vote(
        self, overrides_view, continue_interaction, *, nominee_count_text: str = "2", duration_text=None
    ) -> FakeInteraction:
        """Click Continue, submit the modal, then confirm the summary
        screen -- the full remaining path from an already-open Customize
        This Vote overrides screen to an actually-created (or rejected)
        round.
        """
        continue_button = next(
            child for child in overrides_view.children if getattr(child, "label", None) == "Continue to Vote Settings"
        )
        await continue_button.callback(interaction=continue_interaction)
        modal = continue_interaction.response.sent_modal
        modal.nominee_count_input._value = nominee_count_text
        modal.duration_input._value = duration_text

        submit_interaction = self._interaction()
        await modal.on_submit(interaction=submit_interaction)
        return await self._confirm_customize_vote(submit_interaction)

    async def test_member_filter_narrows_the_created_rounds_candidates(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(menu_view, self._kc_member())
        await self._back_to_overrides(menu_view)

        confirm_interaction = await self._complete_customize_vote(overrides_view, self._interaction())

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round, confirm_interaction.response.sent_message)
        self.assertEqual(vote_round.filter_member_discord_user_id, 111)
        candidates = [
            self.suggestion_service.get_suggestion(candidate_id)
            for candidate_id in vote_round.candidate_suggestion_ids
        ]
        self.assertTrue(all(candidate.journey.original_suggester == "111" for candidate in candidates))

    async def test_genre_filter_narrows_the_created_rounds_candidates(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._select_genre(menu_view, "Comedy")
        await self._back_to_overrides(menu_view)

        confirm_interaction = await self._complete_customize_vote(overrides_view, self._interaction())

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round, confirm_interaction.response.sent_message)
        self.assertEqual(vote_round.filter_genre, "Comedy")
        candidates = [
            self.suggestion_service.get_suggestion(candidate_id)
            for candidate_id in vote_round.candidate_suggestion_ids
        ]
        self.assertTrue(all("Comedy" in candidate.genres for candidate in candidates))

    async def test_combined_member_and_genre_filters_narrow_together(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(menu_view, self._kc_member())
        menu_view = await self._select_genre(menu_view, "Comedy")
        await self._back_to_overrides(menu_view)

        confirm_interaction = await self._complete_customize_vote(overrides_view, self._interaction())

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round, confirm_interaction.response.sent_message)
        self.assertEqual(vote_round.filter_member_discord_user_id, 111)
        self.assertEqual(vote_round.filter_genre, "Comedy")

    async def test_member_filter_insufficient_pool_blocks_round_creation(self) -> None:
        # Custom Vote UX Polish: the insufficient-pool check now runs
        # eagerly at modal-submit time (before Review This Vote is ever
        # shown), so the round is rejected immediately with a recovery
        # screen rather than reaching a "Start Vote" confirm button.
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(menu_view, self._kc_member())
        await self._back_to_overrides(menu_view)

        continue_button = next(
            child for child in overrides_view.children if getattr(child, "label", None) == "Continue to Vote Settings"
        )
        continue_interaction = self._interaction()
        await continue_button.callback(interaction=continue_interaction)
        modal = continue_interaction.response.sent_modal
        modal.nominee_count_input._value = "3"
        modal.duration_input._value = None

        submit_interaction = self._interaction()
        await modal.on_submit(interaction=submit_interaction)

        self.assertIsNone(self.vote_service.get_open_round())
        self.assertTrue(submit_interaction.response.sent_ephemeral)
        self.assertIn("KC has 2 eligible suggestions", submit_interaction.response.sent_message)
        self.assertIn("requires 3 nominees", submit_interaction.response.sent_message)
        self.assertIn("Reduce the candidate count or choose another member", submit_interaction.response.sent_message)
        self.assertIsInstance(submit_interaction.response.sent_view, InsufficientFilteredPoolView)

    async def test_genre_filter_insufficient_pool_blocks_round_creation(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._select_genre(menu_view, "Horror")
        await self._back_to_overrides(menu_view)

        continue_button = next(
            child for child in overrides_view.children if getattr(child, "label", None) == "Continue to Vote Settings"
        )
        continue_interaction = self._interaction()
        await continue_button.callback(interaction=continue_interaction)
        modal = continue_interaction.response.sent_modal
        modal.nominee_count_input._value = "3"
        modal.duration_input._value = None

        submit_interaction = self._interaction()
        await modal.on_submit(interaction=submit_interaction)

        self.assertIsNone(self.vote_service.get_open_round())
        self.assertTrue(submit_interaction.response.sent_ephemeral)
        self.assertIn("The Horror filter leaves 1 eligible suggestion", submit_interaction.response.sent_message)
        self.assertIn("requires 3 nominees", submit_interaction.response.sent_message)
        self.assertIn("Reduce the candidate count or choose another genre", submit_interaction.response.sent_message)
        self.assertIsInstance(submit_interaction.response.sent_view, InsufficientFilteredPoolView)

    async def test_insufficient_pool_recovery_back_button_returns_to_overrides_with_filter_preserved(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._select_genre(menu_view, "Horror")
        await self._back_to_overrides(menu_view)

        continue_button = next(
            child for child in overrides_view.children if getattr(child, "label", None) == "Continue to Vote Settings"
        )
        continue_interaction = self._interaction()
        await continue_button.callback(interaction=continue_interaction)
        modal = continue_interaction.response.sent_modal
        modal.nominee_count_input._value = "3"
        modal.duration_input._value = None
        submit_interaction = self._interaction()
        await modal.on_submit(interaction=submit_interaction)
        recovery_view = submit_interaction.response.sent_view

        back_button = next(
            child for child in recovery_view.children
            if getattr(child, "custom_id", None) == "wpm_start_vote_insufficient_back"
        )
        back_interaction = self._interaction()
        await back_button.callback(interaction=back_interaction)

        self.assertIs(back_interaction.response.edited_view, overrides_view)
        self.assertIn("Horror", back_interaction.response.edited_content)

    async def test_insufficient_pool_recovery_change_filters_reopens_the_filter_menu(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._select_genre(menu_view, "Horror")
        await self._back_to_overrides(menu_view)

        continue_button = next(
            child for child in overrides_view.children if getattr(child, "label", None) == "Continue to Vote Settings"
        )
        continue_interaction = self._interaction()
        await continue_button.callback(interaction=continue_interaction)
        modal = continue_interaction.response.sent_modal
        modal.nominee_count_input._value = "3"
        modal.duration_input._value = None
        submit_interaction = self._interaction()
        await modal.on_submit(interaction=submit_interaction)
        recovery_view = submit_interaction.response.sent_view

        change_filters_button = next(
            child for child in recovery_view.children
            if getattr(child, "custom_id", None) == "wpm_start_vote_insufficient_change_filters"
        )
        change_filters_interaction = self._interaction()
        await change_filters_button.callback(interaction=change_filters_interaction)

        self.assertIn("Horror", change_filters_interaction.response.edited_content)

    async def test_insufficient_pool_recovery_cancel_clears_the_view(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._select_genre(menu_view, "Horror")
        await self._back_to_overrides(menu_view)

        continue_button = next(
            child for child in overrides_view.children if getattr(child, "label", None) == "Continue to Vote Settings"
        )
        continue_interaction = self._interaction()
        await continue_button.callback(interaction=continue_interaction)
        modal = continue_interaction.response.sent_modal
        modal.nominee_count_input._value = "3"
        modal.duration_input._value = None
        submit_interaction = self._interaction()
        await modal.on_submit(interaction=submit_interaction)
        recovery_view = submit_interaction.response.sent_view

        cancel_button = next(
            child for child in recovery_view.children
            if getattr(child, "custom_id", None) == "wpm_start_vote_insufficient_cancel"
        )
        cancel_interaction = self._interaction()
        await cancel_button.callback(interaction=cancel_interaction)

        self.assertIn("cancelled", cancel_interaction.response.edited_content)
        self.assertIsNone(cancel_interaction.response.edited_view)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_filters_never_persist_to_the_collections_own_configuration(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(menu_view, self._kc_member())
        await self._back_to_overrides(menu_view)

        await self._complete_customize_vote(overrides_view, self._interaction())

        configuration = self.bot.suggestion_database_configuration_repository.get(100, self.database_id)
        self.assertIsNone(configuration)  # never created/touched by a per-vote filter

    async def test_unfiltered_customize_vote_still_works_unchanged(self) -> None:
        # Regression: Any Member / Any Genre (filter menu never opened)
        # must behave exactly like before this feature existed.
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view

        confirm_interaction = await self._complete_customize_vote(overrides_view, self._interaction())

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round, confirm_interaction.response.sent_message)
        self.assertIsNone(vote_round.filter_member_discord_user_id)
        self.assertIsNone(vote_round.filter_genre)

    # --- Regression coverage for the live bug: a non-member or a member
    # with zero eligible suggestions must never be silently discarded
    # while letting Continue to Vote Settings (and, past it, Review This
    # Vote / round creation) proceed. See services/member_filter_validation.py.

    def _bot_member(self) -> FakeMember:
        # A bot account is judged by the same two rules as a human: Watch
        # Party membership plus at least one eligible suggestion tied to
        # its stable Discord user ID -- never rejected merely for being a
        # bot. 444 has one eligible Comedy suggestion, added below.
        return FakeMember(user_id=444, roles=[FakeRole(self.WATCH_PARTY_FILTER_ROLE_ID)], display_name="Midjourney Bot")

    def _continue_button(self, overrides_view):
        return next(
            child for child in overrides_view.children if getattr(child, "label", None) == "Continue to Vote Settings"
        )

    async def test_non_member_selection_disables_continue(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(
            menu_view, FakeMember(user_id=333, roles=[], display_name="Stranger")
        )

        self.assertTrue(self._menu_continue_button(menu_view).disabled)

        await self._back_to_overrides(menu_view)
        self.assertTrue(overrides_view.continue_button.disabled)

    async def test_zero_eligible_suggestions_selection_disables_continue(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        idle_member = FakeMember(user_id=999, roles=[FakeRole(self.WATCH_PARTY_FILTER_ROLE_ID)], display_name="Idle")
        menu_view = await self._set_member_and_return_to_menu(menu_view, idle_member)

        self.assertTrue(self._menu_continue_button(menu_view).disabled)

        await self._back_to_overrides(menu_view)
        self.assertTrue(overrides_view.continue_button.disabled)

    async def test_valid_member_selection_keeps_continue_enabled(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(menu_view, self._kc_member())

        self.assertFalse(self._menu_continue_button(menu_view).disabled)

        await self._back_to_overrides(menu_view)
        self.assertFalse(overrides_view.continue_button.disabled)

    async def test_valid_bot_member_with_eligible_suggestions_is_accepted(self) -> None:
        self.suggestion_service.suggest(
            "Bot-Suggested Comedy", database_id=self.database_id, original_suggester="444", genres=("Comedy",)
        )
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)

        select_interaction = await self._select_member(menu_view, self._bot_member())

        self.assertIn("Midjourney Bot has 1 eligible suggestion", select_interaction.response.edited_content)
        menu_view = await self._back_to_menu(select_interaction.response.edited_view)
        self.assertFalse(self._menu_continue_button(menu_view).disabled)

    async def test_clicking_continue_while_a_non_member_is_selected_is_blocked(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(
            menu_view, FakeMember(user_id=333, roles=[], display_name="Stranger")
        )

        continue_interaction = self._interaction()
        await self._menu_continue_button(menu_view).callback(interaction=continue_interaction)

        self.assertIsNone(continue_interaction.response.sent_modal)
        self.assertIn(
            "is not the server owner, a WASH Crew member, or a current Watch Party member",
            continue_interaction.response.edited_content,
        )
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_clicking_continue_while_a_zero_eligible_member_is_selected_is_blocked(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        idle_member = FakeMember(user_id=999, roles=[FakeRole(self.WATCH_PARTY_FILTER_ROLE_ID)], display_name="Idle")
        menu_view = await self._set_member_and_return_to_menu(menu_view, idle_member)

        continue_interaction = self._interaction()
        await self._menu_continue_button(menu_view).callback(interaction=continue_interaction)

        self.assertIsNone(continue_interaction.response.sent_modal)
        self.assertIn("has no eligible suggestions", continue_interaction.response.edited_content)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_clearing_an_invalid_selection_restores_any_member_and_reenables_continue(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(
            menu_view, FakeMember(user_id=333, roles=[], display_name="Stranger")
        )
        self.assertTrue(self._menu_continue_button(menu_view).disabled)

        menu_view = await self._clear_member_and_return_to_menu(menu_view)

        self.assertFalse(self._menu_continue_button(menu_view).disabled)
        member_option = next(
            option for option in self._category_select(menu_view).options if option.value == FILTER_CATEGORY_MEMBER
        )
        self.assertNotIn("Stranger", member_option.label)

    async def test_invalid_member_is_not_shown_in_review_this_vote(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(
            menu_view, FakeMember(user_id=333, roles=[], display_name="Stranger")
        )
        # Explicitly clear back to Any Member -- the only way to unblock.
        menu_view = await self._clear_member_and_return_to_menu(menu_view)
        menu_view = await self._select_genre(menu_view, "Comedy")

        continue_interaction = self._interaction()
        await self._menu_continue_button(menu_view).callback(interaction=continue_interaction)
        modal = continue_interaction.response.sent_modal
        self.assertIsNotNone(modal)
        modal.nominee_count_input._value = "2"
        modal.duration_input._value = None
        submit_interaction = self._interaction()
        await modal.on_submit(interaction=submit_interaction)

        self.assertNotIn("Stranger", submit_interaction.response.sent_message)
        self.assertIn("Genre: Comedy", submit_interaction.response.sent_message)

    async def test_invalid_member_is_never_persisted_to_the_vote_round(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(
            menu_view, FakeMember(user_id=333, roles=[], display_name="Stranger")
        )
        menu_view = await self._clear_member_and_return_to_menu(menu_view)
        await self._back_to_overrides(menu_view)

        confirm_interaction = await self._complete_customize_vote(overrides_view, self._interaction())

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round, confirm_interaction.response.sent_message)
        self.assertIsNone(vote_round.filter_member_discord_user_id)

    async def test_genre_only_vote_still_works_after_member_filter_explicitly_cleared(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(menu_view, self._kc_member())
        menu_view = await self._clear_member_and_return_to_menu(menu_view)
        menu_view = await self._select_genre(menu_view, "Comedy")
        await self._back_to_overrides(menu_view)

        confirm_interaction = await self._complete_customize_vote(overrides_view, self._interaction())

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round, confirm_interaction.response.sent_message)
        self.assertIsNone(vote_round.filter_member_discord_user_id)
        self.assertEqual(vote_round.filter_genre, "Comedy")

    async def test_recovering_from_an_invalid_selection_with_a_valid_member_persists_only_the_valid_one(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(
            menu_view, FakeMember(user_id=333, roles=[], display_name="Stranger")
        )
        menu_view = await self._set_member_and_return_to_menu(menu_view, self._kc_member())
        await self._back_to_overrides(menu_view)

        confirm_interaction = await self._complete_customize_vote(overrides_view, self._interaction())

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round, confirm_interaction.response.sent_message)
        self.assertEqual(vote_round.filter_member_discord_user_id, 111)

    # --- Expanded member validation: server owner / WASH Crew / Watch Party ------

    async def test_server_owner_is_a_valid_member_even_without_any_configured_role(self) -> None:
        from types import SimpleNamespace

        self.suggestion_service.suggest(
            "Owner's Pick", database_id=self.database_id, original_suggester="555", genres=("Comedy",)
        )
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        owner = FakeMember(user_id=555, roles=[], display_name="HeidiTheGreat")

        select_interaction = await self._select_member(menu_view, owner, guild=SimpleNamespace(owner_id=555))

        self.assertIn("HeidiTheGreat has 1 eligible suggestion", select_interaction.response.edited_content)
        menu_view = await self._back_to_menu(select_interaction.response.edited_view)
        self.assertFalse(self._menu_continue_button(menu_view).disabled)

    async def test_wash_crew_member_is_a_valid_member_even_without_the_watch_party_role(self) -> None:
        from types import SimpleNamespace

        self.suggestion_service.suggest(
            "Crew's Pick", database_id=self.database_id, original_suggester="666", genres=("Comedy",)
        )
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        crew_member = FakeMember(user_id=666, roles=[FakeRole(WASH_CREW_ROLE_ID)], display_name="Crew")

        select_interaction = await self._select_member(menu_view, crew_member, guild=SimpleNamespace(owner_id=1))

        self.assertIn("Crew has 1 eligible suggestion", select_interaction.response.edited_content)
        menu_view = await self._back_to_menu(select_interaction.response.edited_view)
        self.assertFalse(self._menu_continue_button(menu_view).disabled)

    async def test_owner_with_zero_eligible_suggestions_is_still_invalid(self) -> None:
        from types import SimpleNamespace

        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        owner = FakeMember(user_id=555, roles=[], display_name="HeidiTheGreat")

        select_interaction = await self._select_member(menu_view, owner, guild=SimpleNamespace(owner_id=555))

        self.assertIn("HeidiTheGreat has no eligible suggestions", select_interaction.response.edited_content)
        menu_view = await self._back_to_menu(select_interaction.response.edited_view)
        self.assertTrue(self._menu_continue_button(menu_view).disabled)

    # --- Filter summary: scalable "Current Filters" block ------------------------

    async def test_filter_summary_shows_any_member_and_any_genre_by_default(self) -> None:
        interaction = await self._open_customize_screen()

        message = interaction.response.sent_message
        self.assertIn("Current Filters", message)
        self.assertIn("Member ......... Any Member", message)
        self.assertIn("Genre .......... Any Genre", message)

    async def test_filter_summary_shows_the_active_member(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        select_interaction = await self._select_member(menu_view, self._kc_member())

        back_interaction = self._interaction()
        await self._back_to_menu_button(select_interaction.response.edited_view).callback(interaction=back_interaction)

        self.assertIn("Member ......... KC", back_interaction.response.edited_content)
        self.assertIn("Genre .......... Any Genre", back_interaction.response.edited_content)

    async def test_filter_summary_reverts_to_any_member_once_cleared(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(menu_view, self._kc_member())

        clear_interaction = await self._clear_member(menu_view)
        back_interaction = self._interaction()
        await self._back_to_menu_button(clear_interaction.response.edited_view).callback(interaction=back_interaction)

        self.assertIn("Member ......... Any Member", back_interaction.response.edited_content)

    async def test_filter_summary_shows_both_active_filters_together(self) -> None:
        interaction = await self._open_customize_screen()
        overrides_view = interaction.response.sent_view
        menu_view = await self._open_filter_menu(overrides_view)
        menu_view = await self._set_member_and_return_to_menu(menu_view, self._kc_member())

        genre_edit_view = await self._open_category_edit(menu_view, FILTER_CATEGORY_GENRE)
        self._genre_select(genre_edit_view)._values = ["Comedy"]
        genre_interaction = self._interaction()
        await self._genre_select(genre_edit_view).callback(interaction=genre_interaction)

        self.assertIn("Member ......... KC", genre_interaction.response.edited_content)
        self.assertIn("Genre .......... Comedy", genre_interaction.response.edited_content)


if __name__ == "__main__":
    unittest.main()
