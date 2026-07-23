"""Tests for FR-028's /setup wizard domain model (domain/setup_wizard.py)."""

import unittest
from datetime import datetime

from watch_party_manager.domain.setup_wizard import (
    SETUP_WIZARD_STEP_ORDER,
    SetupWizardDraft,
    SetupWizardState,
    SetupWizardStatus,
    SetupWizardStep,
)


class SetupWizardDraftTests(unittest.TestCase):
    def test_defaults_are_all_unanswered(self):
        draft = SetupWizardDraft()
        self.assertIsNone(draft.wash_crew_role_id)
        self.assertIsNone(draft.suggestion_database_id)
        self.assertFalse(draft.watch_destination_skipped)
        self.assertFalse(draft.suggestion_database_is_new)

    def test_rejects_non_positive_snowflake_ids(self):
        with self.assertRaises(ValueError):
            SetupWizardDraft(wash_crew_role_id=0)
        with self.assertRaises(ValueError):
            SetupWizardDraft(watch_party_role_id=-1)
        with self.assertRaises(ValueError):
            SetupWizardDraft(suggestion_database_id=-5)
        with self.assertRaises(ValueError):
            SetupWizardDraft(watch_destination_channel_id=0)

    def test_accepts_positive_snowflake_ids(self):
        draft = SetupWizardDraft(wash_crew_role_id=111, watch_party_role_id=222)
        self.assertEqual(draft.wash_crew_role_id, 111)
        self.assertEqual(draft.watch_party_role_id, 222)


class SetupWizardStateTests(unittest.TestCase):
    def test_defaults_to_first_step_and_in_progress(self):
        state = SetupWizardState(guild_id=1)
        self.assertEqual(state.current_step, SetupWizardStep.WASH_CREW_ROLE)
        self.assertEqual(state.status, SetupWizardStatus.IN_PROGRESS)
        self.assertEqual(state.completed_steps, ())

    def test_rejects_non_positive_guild_id(self):
        with self.assertRaises(ValueError):
            SetupWizardState(guild_id=0)
        with self.assertRaises(ValueError):
            SetupWizardState(guild_id=-1)

    def test_requires_timezone_aware_timestamps(self):
        naive = datetime(2026, 1, 1)
        with self.assertRaises(ValueError):
            SetupWizardState(guild_id=1, started_at=naive)
        with self.assertRaises(ValueError):
            SetupWizardState(guild_id=1, updated_at=naive)

    def test_rejects_duplicate_completed_steps(self):
        with self.assertRaises(ValueError):
            SetupWizardState(
                guild_id=1,
                completed_steps=(SetupWizardStep.WASH_CREW_ROLE, SetupWizardStep.WASH_CREW_ROLE),
            )

    def test_with_step_completed_appends_new_step(self):
        state = SetupWizardState(guild_id=1)
        result = state.with_step_completed(SetupWizardStep.WASH_CREW_ROLE)
        self.assertEqual(result, (SetupWizardStep.WASH_CREW_ROLE,))
        # Original state is untouched -- with_step_completed only returns
        # the new tuple, it doesn't mutate the state itself.
        self.assertEqual(state.completed_steps, ())

    def test_with_step_completed_does_not_duplicate(self):
        state = SetupWizardState(guild_id=1, completed_steps=(SetupWizardStep.WASH_CREW_ROLE,))
        result = state.with_step_completed(SetupWizardStep.WASH_CREW_ROLE)
        self.assertEqual(result, (SetupWizardStep.WASH_CREW_ROLE,))


class SetupWizardStepOrderTests(unittest.TestCase):
    def test_order_contains_every_step_exactly_once(self):
        self.assertEqual(set(SETUP_WIZARD_STEP_ORDER), set(SetupWizardStep))
        self.assertEqual(len(SETUP_WIZARD_STEP_ORDER), len(set(SETUP_WIZARD_STEP_ORDER)))

    def test_review_is_last(self):
        self.assertEqual(SETUP_WIZARD_STEP_ORDER[-1], SetupWizardStep.REVIEW)


if __name__ == "__main__":
    unittest.main()
