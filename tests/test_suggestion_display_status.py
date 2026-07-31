import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import date

from watch_party_manager.domain.watch_item import MediaType, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.services.suggestion_display_status import (
    SUGGESTION_DISPLAY_STATUS_EMOJI,
    SUGGESTION_DISPLAY_STATUS_LABELS,
    SuggestionDisplayStatus,
    compute_display_status,
    display_status_label,
    format_display_status_with_won_date,
    format_won_date,
    resolve_display_status,
    vote_winner_won_date_line,
    watched_date_line,
)


def make_item(status: WatchItemStatus = WatchItemStatus.SUGGESTED) -> WatchItem:
    return WatchItem(title="Alien", media_type=MediaType.MOVIE, status=status)


class ComputeDisplayStatusTests(unittest.TestCase):
    def test_archived_is_retired_regardless_of_cooldown(self) -> None:
        item = make_item(WatchItemStatus.ARCHIVED)
        self.assertEqual(
            SuggestionDisplayStatus.RETIRED, compute_display_status(item, in_rotation_cooldown=True)
        )
        self.assertEqual(
            SuggestionDisplayStatus.RETIRED, compute_display_status(item, in_rotation_cooldown=False)
        )

    def test_vote_winner_is_vote_winner_regardless_of_cooldown(self) -> None:
        item = make_item(WatchItemStatus.VOTE_WINNER)
        self.assertEqual(
            SuggestionDisplayStatus.VOTE_WINNER, compute_display_status(item, in_rotation_cooldown=True)
        )
        self.assertEqual(
            SuggestionDisplayStatus.VOTE_WINNER, compute_display_status(item, in_rotation_cooldown=False)
        )

    def test_suggested_in_cooldown_is_rotation_cooldown(self) -> None:
        item = make_item(WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.ROTATION_COOLDOWN, compute_display_status(item, in_rotation_cooldown=True)
        )

    def test_suggested_not_in_cooldown_is_available(self) -> None:
        item = make_item(WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.AVAILABLE, compute_display_status(item, in_rotation_cooldown=False)
        )

    def test_watched_is_watched_regardless_of_cooldown(self) -> None:
        item = make_item(WatchItemStatus.WATCHED)
        self.assertEqual(
            SuggestionDisplayStatus.WATCHED, compute_display_status(item, in_rotation_cooldown=True)
        )
        self.assertEqual(
            SuggestionDisplayStatus.WATCHED, compute_display_status(item, in_rotation_cooldown=False)
        )

    def test_suggested_in_an_active_vote_is_in_active_vote(self) -> None:
        # Rotation-removal Phase 1.
        item = make_item(WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.IN_ACTIVE_VOTE,
            compute_display_status(item, in_rotation_cooldown=False, in_active_vote=True),
        )

    def test_in_an_active_vote_takes_precedence_over_rotation_cooldown(self) -> None:
        item = make_item(WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.IN_ACTIVE_VOTE,
            compute_display_status(item, in_rotation_cooldown=True, in_active_vote=True),
        )

    def test_in_active_vote_defaults_to_false(self) -> None:
        # Backward-compatible optional parameter: every pre-existing call
        # site that doesn't yet pass in_active_vote must behave exactly
        # as before.
        item = make_item(WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.AVAILABLE, compute_display_status(item, in_rotation_cooldown=False)
        )

    def test_archived_is_retired_even_in_an_active_vote(self) -> None:
        item = make_item(WatchItemStatus.ARCHIVED)
        self.assertEqual(
            SuggestionDisplayStatus.RETIRED,
            compute_display_status(item, in_rotation_cooldown=False, in_active_vote=True),
        )

    def test_vote_winner_is_vote_winner_even_in_an_active_vote(self) -> None:
        item = make_item(WatchItemStatus.VOTE_WINNER)
        self.assertEqual(
            SuggestionDisplayStatus.VOTE_WINNER,
            compute_display_status(item, in_rotation_cooldown=False, in_active_vote=True),
        )

    def test_watched_is_watched_even_in_an_active_vote(self) -> None:
        item = make_item(WatchItemStatus.WATCHED)
        self.assertEqual(
            SuggestionDisplayStatus.WATCHED,
            compute_display_status(item, in_rotation_cooldown=False, in_active_vote=True),
        )


class ResolveDisplayStatusTests(unittest.TestCase):
    class _FakeRotationService:
        def __init__(self, cooldown: bool) -> None:
            self._cooldown = cooldown

        def is_in_rotation_cooldown(self, watch_item) -> bool:
            return self._cooldown

    def test_uses_the_rotation_service_when_provided(self) -> None:
        item = make_item(WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.ROTATION_COOLDOWN,
            resolve_display_status(item, self._FakeRotationService(True)),
        )

    def test_defaults_to_available_with_no_rotation_service(self) -> None:
        item = make_item(WatchItemStatus.SUGGESTED)
        self.assertEqual(SuggestionDisplayStatus.AVAILABLE, resolve_display_status(item, None))

    class _FakeVoteService:
        def __init__(self, open_round) -> None:
            self._open_round = open_round

        def get_open_round_for_suggestion(self, suggestion_id):
            return self._open_round

    def test_in_an_active_vote_when_the_vote_service_reports_an_open_round(self) -> None:
        item = WatchItem(id=1, title="Alien", media_type=MediaType.MOVIE, status=WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.IN_ACTIVE_VOTE,
            resolve_display_status(item, None, self._FakeVoteService(open_round=object())),
        )

    def test_not_in_an_active_vote_when_the_vote_service_reports_no_open_round(self) -> None:
        item = WatchItem(id=1, title="Alien", media_type=MediaType.MOVIE, status=WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.AVAILABLE,
            resolve_display_status(item, None, self._FakeVoteService(open_round=None)),
        )

    def test_defaults_to_not_in_an_active_vote_with_no_vote_service(self) -> None:
        item = WatchItem(id=1, title="Alien", media_type=MediaType.MOVIE, status=WatchItemStatus.SUGGESTED)
        self.assertEqual(SuggestionDisplayStatus.AVAILABLE, resolve_display_status(item, None))

    def test_in_an_active_vote_never_fires_for_an_item_with_no_id(self) -> None:
        # An unsaved WatchItem (id=None) can never be an actual vote
        # candidate -- must not error, must resolve as not-in-a-vote.
        item = WatchItem(title="Alien", media_type=MediaType.MOVIE, status=WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.AVAILABLE,
            resolve_display_status(item, None, self._FakeVoteService(open_round=object())),
        )

    def test_in_an_active_vote_takes_precedence_over_rotation_cooldown(self) -> None:
        item = WatchItem(id=1, title="Alien", media_type=MediaType.MOVIE, status=WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.IN_ACTIVE_VOTE,
            resolve_display_status(item, self._FakeRotationService(True), self._FakeVoteService(open_round=object())),
        )


class DisplayStatusLabelTests(unittest.TestCase):
    def test_every_status_has_a_label(self) -> None:
        for status in SuggestionDisplayStatus:
            self.assertIn(status, SUGGESTION_DISPLAY_STATUS_LABELS)

    def test_labels_match_the_specified_emoji_and_wording(self) -> None:
        self.assertEqual("🟢 Available", display_status_label(SuggestionDisplayStatus.AVAILABLE))
        self.assertEqual(
            "🟡 Rotation Cooldown", display_status_label(SuggestionDisplayStatus.ROTATION_COOLDOWN)
        )
        self.assertEqual("🏆 Vote Winner", display_status_label(SuggestionDisplayStatus.VOTE_WINNER))
        self.assertEqual("🗄️ Retired", display_status_label(SuggestionDisplayStatus.RETIRED))
        self.assertEqual("✅ Watched", display_status_label(SuggestionDisplayStatus.WATCHED))
        self.assertEqual(
            "🗳️ In an Active Vote", display_status_label(SuggestionDisplayStatus.IN_ACTIVE_VOTE)
        )

    def test_every_status_has_a_bare_emoji(self) -> None:
        for status in SuggestionDisplayStatus:
            self.assertIn(status, SUGGESTION_DISPLAY_STATUS_EMOJI)

    def test_bare_emoji_matches_the_label(self) -> None:
        for status in SuggestionDisplayStatus:
            self.assertTrue(
                display_status_label(status).startswith(SUGGESTION_DISPLAY_STATUS_EMOJI[status])
            )


class FormatWonDateTests(unittest.TestCase):
    def test_formats_month_day_year_with_no_leading_zero(self) -> None:
        self.assertEqual("July 28, 2026", format_won_date(date(2026, 7, 28)))

    def test_formats_single_digit_day_without_a_leading_zero(self) -> None:
        self.assertEqual("July 8, 2026", format_won_date(date(2026, 7, 8)))

    def test_never_includes_a_time(self) -> None:
        rendered = format_won_date(date(2026, 7, 28))
        self.assertNotIn(":", rendered)


class VoteWinnerWonDateLineTests(unittest.TestCase):
    def test_returns_the_won_line_when_a_date_is_recorded(self) -> None:
        item = make_item(WatchItemStatus.VOTE_WINNER)
        item.journey = WatchItemJourney(last_won_date=date(2026, 7, 28))

        self.assertEqual("Won: July 28, 2026", vote_winner_won_date_line(item))

    def test_returns_none_for_a_legacy_winner_with_no_recorded_date(self) -> None:
        item = make_item(WatchItemStatus.VOTE_WINNER)

        self.assertIsNone(vote_winner_won_date_line(item))


class WatchedDateLineTests(unittest.TestCase):
    def test_returns_the_watched_line_for_the_most_recent_date(self) -> None:
        item = make_item(WatchItemStatus.WATCHED)
        item.journey = WatchItemJourney(watch_dates=(date(2026, 6, 1), date(2026, 7, 28)))

        self.assertEqual("Watched: July 28, 2026", watched_date_line(item))

    def test_returns_none_when_no_watch_date_is_recorded(self) -> None:
        item = make_item(WatchItemStatus.WATCHED)

        self.assertIsNone(watched_date_line(item))


class FormatDisplayStatusWithWonDateTests(unittest.TestCase):
    def test_vote_winner_with_a_date_appends_the_won_line(self) -> None:
        item = make_item(WatchItemStatus.VOTE_WINNER)
        item.journey = WatchItemJourney(last_won_date=date(2026, 7, 28))

        result = format_display_status_with_won_date(item, SuggestionDisplayStatus.VOTE_WINNER)

        self.assertEqual("🏆 Vote Winner\nWon: July 28, 2026", result)

    def test_legacy_vote_winner_without_a_date_omits_the_won_line_gracefully(self) -> None:
        item = make_item(WatchItemStatus.VOTE_WINNER)

        result = format_display_status_with_won_date(item, SuggestionDisplayStatus.VOTE_WINNER)

        self.assertEqual("🏆 Vote Winner", result)

    def test_non_vote_winner_statuses_are_unaffected(self) -> None:
        item = make_item(WatchItemStatus.ARCHIVED)

        result = format_display_status_with_won_date(item, SuggestionDisplayStatus.RETIRED)

        self.assertEqual("🗄️ Retired", result)

    def test_watched_with_a_date_appends_the_watched_line(self) -> None:
        item = make_item(WatchItemStatus.WATCHED)
        item.journey = WatchItemJourney(watch_dates=(date(2026, 7, 28),))

        result = format_display_status_with_won_date(item, SuggestionDisplayStatus.WATCHED)

        self.assertEqual("✅ Watched\nWatched: July 28, 2026", result)

    def test_watched_without_a_date_omits_the_watched_line_gracefully(self) -> None:
        item = make_item(WatchItemStatus.WATCHED)

        result = format_display_status_with_won_date(item, SuggestionDisplayStatus.WATCHED)

        self.assertEqual("✅ Watched", result)


if __name__ == "__main__":
    unittest.main()
