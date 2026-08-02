"""Unit tests for services/browse_service.py -- /browse's own richer
result-line formatting and collection-change filter revalidation. Pure,
Discord-free logic; the Discord-facing command flow (scope, filters,
pagination, Random Pick, Start Vote, Post Publicly) is covered in
test_browse_command.py.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.browse_service import build_browse_entry_line, revalidate_browse_filter_state
from watch_party_manager.services.suggestion_display_status import SuggestionDisplayStatus
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100


def new_filter_state(**overrides) -> dict:
    state = {
        "genre": None,
        "imdb_rating_min": None,
        "imdb_rating_max": None,
        "mpaa_rating": None,
        "actor": None,
        "member_id": None,
        "member_display": None,
        "member_filter_invalid": False,
        "member_filter_status_line": "",
    }
    state.update(overrides)
    return state


class BrowseServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database("Movies", guild_id=GUILD_ID, channel_id=10).database

    def _add(self, title: str, **kwargs):
        return self.suggestion_service.suggest(
            title, database_id=self.database.database_id, guild_id=GUILD_ID, **kwargs
        ).watch_item


class BuildBrowseEntryLineTests(BrowseServiceTestCase):
    def test_includes_title_year_and_reference(self) -> None:
        item = self._add("Alien", release_year=1979)

        line = build_browse_entry_line(item, SuggestionDisplayStatus.AVAILABLE)

        self.assertIn("Alien", line)
        self.assertIn("1979", line)
        self.assertIn(item.reference, line)

    def test_includes_imdb_rating_mpaa_rating_and_genres_when_present(self) -> None:
        item = self._add(
            "Alien",
            release_year=1979,
            imdb_rating="8.5",
            content_rating="R",
            genres=("Horror", "Sci-Fi"),
        )

        line = build_browse_entry_line(item, SuggestionDisplayStatus.AVAILABLE)

        self.assertIn("IMDb 8.5/10", line)
        self.assertIn("R", line)
        self.assertIn("Horror, Sci-Fi", line)

    def test_omits_missing_metadata_fields_without_error(self) -> None:
        item = self._add("Untitled")

        line = build_browse_entry_line(item, SuggestionDisplayStatus.AVAILABLE)

        self.assertIn("Untitled", line)
        self.assertNotIn("IMDb", line)

    def test_includes_suggester_mention_when_known(self) -> None:
        item = self._add("Alien", original_suggester="123456789012345678")

        line = build_browse_entry_line(item, SuggestionDisplayStatus.AVAILABLE)

        self.assertIn("<@123456789012345678>", line)

    def test_unknown_suggester_shown_as_unknown(self) -> None:
        item = self._add("Alien")

        line = build_browse_entry_line(item, SuggestionDisplayStatus.AVAILABLE)

        self.assertIn("Unknown", line)

    def test_includes_the_status_label(self) -> None:
        item = self._add("Alien")

        line = build_browse_entry_line(item, SuggestionDisplayStatus.AVAILABLE)

        self.assertIn("Available", line)

    def test_status_emoji_matches_the_given_display_status(self) -> None:
        item = self._add("Alien")

        line = build_browse_entry_line(item, SuggestionDisplayStatus.PENDING_CREW_REVIEW)

        self.assertIn("⚠️ Pending Crew Review", line)
        # No redundant second status emoji leading the line.
        self.assertFalse(line.startswith("⚠️"))


class RevalidateBrowseFilterStateTests(BrowseServiceTestCase):
    def test_genre_still_present_in_new_pool_is_kept(self) -> None:
        item = self._add("Alien", genres=("Horror",))
        state = new_filter_state(genre="Horror")

        result = revalidate_browse_filter_state(state, [item])

        self.assertEqual(result["genre"], "Horror")

    def test_genre_absent_from_new_pool_is_cleared(self) -> None:
        item = self._add("Alien", genres=("Horror",))
        state = new_filter_state(genre="Comedy")

        result = revalidate_browse_filter_state(state, [item])

        self.assertIsNone(result["genre"])

    def test_mpaa_rating_absent_from_new_pool_is_cleared(self) -> None:
        item = self._add("Alien", content_rating="R")
        state = new_filter_state(mpaa_rating="PG")

        result = revalidate_browse_filter_state(state, [item])

        self.assertIsNone(result["mpaa_rating"])

    def test_mpaa_rating_present_in_new_pool_is_kept(self) -> None:
        item = self._add("Alien", content_rating="R")
        state = new_filter_state(mpaa_rating="R")

        result = revalidate_browse_filter_state(state, [item])

        self.assertEqual(result["mpaa_rating"], "R")

    def test_actor_absent_from_new_pool_is_cleared(self) -> None:
        item = self._add("Alien", cast=("Sigourney Weaver",))
        state = new_filter_state(actor="Tom Hanks")

        result = revalidate_browse_filter_state(state, [item])

        self.assertIsNone(result["actor"])

    def test_actor_present_in_new_pool_is_kept(self) -> None:
        item = self._add("Alien", cast=("Sigourney Weaver",))
        state = new_filter_state(actor="Sigourney Weaver")

        result = revalidate_browse_filter_state(state, [item])

        self.assertEqual(result["actor"], "Sigourney Weaver")

    def test_member_with_no_eligible_suggestion_in_new_pool_is_cleared(self) -> None:
        item = self._add("Alien", original_suggester="1")
        state = new_filter_state(member_id=2, member_display="Someone Else")

        result = revalidate_browse_filter_state(state, [item])

        self.assertIsNone(result["member_id"])
        self.assertIsNone(result["member_display"])

    def test_member_with_an_eligible_suggestion_in_new_pool_is_kept(self) -> None:
        item = self._add("Alien", original_suggester="1")
        state = new_filter_state(member_id=1, member_display="KC")

        result = revalidate_browse_filter_state(state, [item])

        self.assertEqual(result["member_id"], 1)
        self.assertEqual(result["member_display"], "KC")

    def test_imdb_rating_range_is_never_cleared(self) -> None:
        item = self._add("Alien")
        state = new_filter_state(imdb_rating_min=7.0, imdb_rating_max=9.0)

        result = revalidate_browse_filter_state(state, [item])

        self.assertEqual(result["imdb_rating_min"], 7.0)
        self.assertEqual(result["imdb_rating_max"], 9.0)

    def test_inactive_filters_stay_inactive(self) -> None:
        item = self._add("Alien")
        state = new_filter_state()

        result = revalidate_browse_filter_state(state, [item])

        self.assertEqual(result, state)

    def test_does_not_mutate_the_original_state(self) -> None:
        item = self._add("Alien", genres=("Horror",))
        state = new_filter_state(genre="Comedy")

        revalidate_browse_filter_state(state, [item])

        self.assertEqual(state["genre"], "Comedy")

    def test_empty_new_pool_clears_every_enumerated_filter(self) -> None:
        state = new_filter_state(genre="Horror", mpaa_rating="R", actor="Sigourney Weaver", member_id=1, member_display="KC")

        result = revalidate_browse_filter_state(state, [])

        self.assertIsNone(result["genre"])
        self.assertIsNone(result["mpaa_rating"])
        self.assertIsNone(result["actor"])
        self.assertIsNone(result["member_id"])


if __name__ == "__main__":
    unittest.main()
