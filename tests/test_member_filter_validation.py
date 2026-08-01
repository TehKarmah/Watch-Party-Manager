"""Unit tests for services/member_filter_validation.py -- the single
shared rule /vote start's Custom Vote Filters and /random_watch's
filters both use to decide whether a Suggestion Source selection is
valid. See the live-bug fix this module exists to prevent: an invalid
member selection must never be silently discarded while letting a flow
continue as though Any Member had been chosen.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_item import MediaType, WatchItem, WatchItemJourney
from watch_party_manager.services.member_filter_validation import (
    MemberFilterSelectionResult,
    validate_member_filter_selection,
)

WATCH_PARTY_ROLE_ID = 777


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int, *, roles=(), display_name: str = "Member") -> None:
        self.id = user_id
        self.roles = list(roles)
        self.display_name = display_name


def _item(item_id: int, *, original_suggester=None) -> WatchItem:
    return WatchItem(
        title=f"Movie {item_id}",
        media_type=MediaType.MOVIE,
        id=item_id,
        journey=WatchItemJourney(original_suggester=original_suggester),
    )


class ValidateMemberFilterSelectionTests(unittest.TestCase):
    def test_none_selection_is_any_member_and_always_valid(self) -> None:
        result = validate_member_filter_selection(
            None, watch_party_role_id=WATCH_PARTY_ROLE_ID, eligible_pool=[], collection_display_name="Movies"
        )

        self.assertEqual(
            result, MemberFilterSelectionResult(valid=True, discord_user_id=None, member_display=None, status_line="")
        )

    def test_non_member_selection_is_invalid_with_no_silent_fallback_identity(self) -> None:
        pool = [_item(1, original_suggester="333")]
        stranger = FakeMember(333, roles=[], display_name="Stranger")

        result = validate_member_filter_selection(
            stranger, watch_party_role_id=WATCH_PARTY_ROLE_ID, eligible_pool=pool, collection_display_name="Movies"
        )

        self.assertFalse(result.valid)
        self.assertIsNone(result.discord_user_id)
        self.assertIsNone(result.member_display)
        self.assertIn("Stranger", result.status_line)
        self.assertIn("not a current Watch Party member", result.status_line)

    def test_member_with_zero_eligible_suggestions_is_invalid(self) -> None:
        pool = [_item(1, original_suggester="222")]
        idle_member = FakeMember(111, roles=[FakeRole(WATCH_PARTY_ROLE_ID)], display_name="Idle")

        result = validate_member_filter_selection(
            idle_member, watch_party_role_id=WATCH_PARTY_ROLE_ID, eligible_pool=pool, collection_display_name="Movie Suggestions"
        )

        self.assertFalse(result.valid)
        self.assertIsNone(result.discord_user_id)
        self.assertIsNone(result.member_display)
        self.assertIn("Idle has no eligible suggestions in Movie Suggestions", result.status_line)
        self.assertIn("Choose another member or clear the member filter", result.status_line)

    def test_valid_member_with_eligible_suggestions_is_accepted(self) -> None:
        pool = [_item(1, original_suggester="111"), _item(2, original_suggester="111"), _item(3, original_suggester="222")]
        kc = FakeMember(111, roles=[FakeRole(WATCH_PARTY_ROLE_ID)], display_name="KC")

        result = validate_member_filter_selection(
            kc, watch_party_role_id=WATCH_PARTY_ROLE_ID, eligible_pool=pool, collection_display_name="Movies"
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.discord_user_id, 111)
        self.assertEqual(result.member_display, "KC")
        self.assertIn("KC has 2 eligible suggestions", result.status_line)

    def test_valid_bot_member_with_eligible_suggestions_is_accepted(self) -> None:
        # A bot account satisfies the exact same Protocol as a human
        # member -- nothing here inspects `.bot`, so it is judged solely
        # on Watch Party membership and eligible suggestions.
        pool = [_item(1, original_suggester="444")]
        bot_member = FakeMember(444, roles=[FakeRole(WATCH_PARTY_ROLE_ID)], display_name="Midjourney Bot")

        result = validate_member_filter_selection(
            bot_member, watch_party_role_id=WATCH_PARTY_ROLE_ID, eligible_pool=pool, collection_display_name="Movies"
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.discord_user_id, 444)
        self.assertEqual(result.member_display, "Midjourney Bot")

    def test_bot_member_without_watch_party_role_is_rejected_like_any_non_member(self) -> None:
        # Confirms bots are not specially exempted from the membership
        # rule either -- only membership + eligibility decide, in both
        # directions.
        pool = [_item(1, original_suggester="444")]
        bot_member = FakeMember(444, roles=[], display_name="Midjourney Bot")

        result = validate_member_filter_selection(
            bot_member, watch_party_role_id=WATCH_PARTY_ROLE_ID, eligible_pool=pool, collection_display_name="Movies"
        )

        self.assertFalse(result.valid)
        self.assertIn("not a current Watch Party member", result.status_line)

    def test_legacy_suggestion_with_no_stored_id_never_counts_as_eligible(self) -> None:
        pool = [_item(1, original_suggester=None)]
        kc = FakeMember(111, roles=[FakeRole(WATCH_PARTY_ROLE_ID)], display_name="KC")

        result = validate_member_filter_selection(
            kc, watch_party_role_id=WATCH_PARTY_ROLE_ID, eligible_pool=pool, collection_display_name="Movies"
        )

        self.assertFalse(result.valid)
        self.assertIn("has no eligible suggestions", result.status_line)

    def test_no_configured_watch_party_role_rejects_every_selection(self) -> None:
        pool = [_item(1, original_suggester="111")]
        kc = FakeMember(111, roles=[FakeRole(WATCH_PARTY_ROLE_ID)], display_name="KC")

        result = validate_member_filter_selection(
            kc, watch_party_role_id=None, eligible_pool=pool, collection_display_name="Movies"
        )

        self.assertFalse(result.valid)
        self.assertIsNone(result.discord_user_id)

    def test_single_eligible_suggestion_uses_singular_wording(self) -> None:
        pool = [_item(1, original_suggester="111")]
        kc = FakeMember(111, roles=[FakeRole(WATCH_PARTY_ROLE_ID)], display_name="KC")

        result = validate_member_filter_selection(
            kc, watch_party_role_id=WATCH_PARTY_ROLE_ID, eligible_pool=pool, collection_display_name="Movies"
        )

        self.assertIn("KC has 1 eligible suggestion.", result.status_line)
        self.assertNotIn("1 eligible suggestions", result.status_line)


if __name__ == "__main__":
    unittest.main()
