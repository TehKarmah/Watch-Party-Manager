"""Unit tests for services/imdb_metadata_recovery_service.py -- the pure,
Discord-free core of IMDb Metadata Recovery: scope resolution (reused
from IMDb Metadata Refresh), discovery of suggestions missing a usable
IMDb identifier, OMDb search/matching (including the year-mismatch
fallback), and save-then-refresh. Discord-specific concerns (the
manage-menu entry point, scope/confirmation screens, Crew Review
screens, Search Again, progress, the final summary) are covered
separately in test_imdb_metadata_recovery_command.py.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import MetadataProvider
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.imdb_metadata_recovery_service import (
    ImdbMetadataRecoveryService,
    RecoveryResult,
    resolve_recoverable_databases,
)
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService, ImdbSearchMatch
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_A = 100
GUILD_B = 200

ALIEN_PAYLOAD = {
    "Response": "True",
    "Title": "Alien",
    "Year": "1979",
    "Genre": "Horror, Sci-Fi",
    "Runtime": "117 min",
    "Rated": "R",
    "Director": "Ridley Scott",
    "imdbRating": "8.5",
    "Actors": "Sigourney Weaver, Tom Skerritt",
    "Poster": "https://example.com/alien.jpg",
    "Plot": "A crew finds a hostile alien.",
}

ALIEN_SEARCH_MATCH = {"Title": "Alien", "Year": "1979", "imdbID": "tt0078748", "Type": "movie"}
ALIEN_1979_REMAKE_MATCH = {"Title": "Alien: The Director's Cut", "Year": "1979", "imdbID": "tt9999991", "Type": "movie"}
ALIENS_1986_MATCH = {"Title": "Aliens", "Year": "1986", "imdbID": "tt0090605", "Type": "movie"}


def make_combined_fetch(
    *,
    search_responses: dict | None = None,
    title_responses: dict | None = None,
    calls: list | None = None,
    raise_for_search: bool = False,
):
    """One fetch_json double covering both OMDb request shapes
    ImdbMetadataService makes: `s=`/`y=` search requests (keyed by
    (title, year) tuples -- year is None when not sent) and `i=` title-
    resolution requests (keyed by imdb_id, exactly like the refresh
    service's own make_fetch helper).
    """
    calls = calls if calls is not None else []
    search_responses = search_responses or {}
    title_responses = title_responses or {}

    def _fetch(url: str):
        calls.append(url)
        params = parse_qs(urlparse(url).query)
        if "s" in params:
            if raise_for_search:
                raise RuntimeError("simulated transient network failure")
            title = params["s"][0]
            year = params.get("y", [None])[0]
            return search_responses.get((title, year), {"Response": "False", "Error": "Movie not found!"})
        if "i" in params:
            imdb_id = params["i"][0]
            return title_responses.get(imdb_id, {"Response": "False", "Error": "Incorrect IMDb ID."})
        return {"Response": "False", "Error": "Bad request."}

    return _fetch


class ImdbMetadataRecoveryServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database("Movies", guild_id=GUILD_A, channel_id=10).database

    def _add(self, title: str, *, imdb_url: str | None = None, database_id: int | None = None, **kwargs):
        return self.suggestion_service.suggest(
            title,
            imdb_url=imdb_url,
            database_id=database_id if database_id is not None else self.database.database_id,
            guild_id=GUILD_A,
            **kwargs,
        ).watch_item


class ResolveRecoverableDatabasesTests(unittest.TestCase):
    def test_filters_to_the_current_guild_only(self) -> None:
        this_guild = SuggestionDatabase(database_id=1, name="A", guild_id=GUILD_A, channel_id=10)
        other_guild = SuggestionDatabase(database_id=2, name="B", guild_id=GUILD_B, channel_id=20)

        result = resolve_recoverable_databases([this_guild, other_guild], guild_id=GUILD_A)

        self.assertEqual(result, [this_guild])

    def test_excludes_inactive_collections(self) -> None:
        active = SuggestionDatabase(database_id=1, name="Active", guild_id=GUILD_A, channel_id=10, active=True)
        inactive = SuggestionDatabase(database_id=2, name="Inactive", guild_id=GUILD_A, channel_id=20, active=False)

        result = resolve_recoverable_databases([active, inactive], guild_id=GUILD_A)

        self.assertEqual(result, [active])


class DiscoveryTests(ImdbMetadataRecoveryServiceTestCase):
    def test_suggestion_without_an_imdb_link_is_missing(self) -> None:
        item = self._add("Untitled")
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=lambda u: {}))

        missing = service.missing_suggestions(self.database.database_id)

        self.assertEqual([suggestion.id for suggestion in missing], [item.id])

    def test_suggestion_with_an_imdb_link_is_excluded(self) -> None:
        self._add("Alien", imdb_url="https://www.imdb.com/title/tt0078748/")
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=lambda u: {}))

        self.assertEqual(service.missing_suggestions(self.database.database_id), [])

    def test_count_missing_makes_no_network_request(self) -> None:
        self._add("Untitled")
        self._add("Alien", imdb_url="https://www.imdb.com/title/tt0078748/")
        calls: list = []
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=make_combined_fetch(calls=calls)))

        missing, total = service.count_missing(self.database.database_id)

        self.assertEqual((missing, total), (1, 2))
        self.assertEqual(calls, [])

    def test_archived_suggestion_missing_a_link_is_still_considered(self) -> None:
        item = self._add("Untitled")
        self.suggestion_service.archive_suggestion(item.id)
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=lambda u: {}))

        missing = service.missing_suggestions(self.database.database_id)

        self.assertEqual([suggestion.id for suggestion in missing], [item.id])

    def test_a_previously_skipped_suggestion_still_appears_in_a_later_scan(self) -> None:
        # Recovery has no "already scanned" flag -- missing_suggestions()
        # is recomputed fresh from current data every time, so a
        # suggestion that was Skipped in an earlier session isn't
        # permanently suppressed.
        item = self._add("Untitled")
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=lambda u: {}))

        first_scan = service.missing_suggestions(self.database.database_id)
        second_scan = service.missing_suggestions(self.database.database_id)

        self.assertEqual([s.id for s in first_scan], [item.id])
        self.assertEqual([s.id for s in second_scan], [item.id])


class FindCandidatesTests(ImdbMetadataRecoveryServiceTestCase):
    async def test_a_single_year_scoped_match_is_returned_without_a_fallback(self) -> None:
        calls: list = []
        fetch = make_combined_fetch(search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]}}, calls=calls)
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))

        search = await service.find_candidates("Alien", 1979)

        self.assertEqual(len(search.matches), 1)
        self.assertEqual(search.matches[0].imdb_id, "tt0078748")
        self.assertIsNone(search.year_mismatch_note)
        self.assertFalse(search.technical_failure)
        self.assertEqual(len(calls), 1)  # no fallback search needed

    async def test_multiple_year_scoped_matches_are_all_returned(self) -> None:
        fetch = make_combined_fetch(
            search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH, ALIEN_1979_REMAKE_MATCH]}}
        )
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))

        search = await service.find_candidates("Alien", 1979)

        self.assertEqual({match.imdb_id for match in search.matches}, {"tt0078748", "tt9999991"})
        self.assertIsNone(search.year_mismatch_note)

    async def test_no_year_scoped_match_falls_back_to_a_title_only_search_with_a_note(self) -> None:
        calls: list = []
        fetch = make_combined_fetch(
            search_responses={
                ("Alien", "1978"): {"Response": "False", "Error": "Movie not found!"},
                ("Alien", None): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]},
            },
            calls=calls,
        )
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))

        search = await service.find_candidates("Alien", 1978)

        self.assertEqual(len(search.matches), 1)
        self.assertIsNotNone(search.year_mismatch_note)
        self.assertIn("1978", search.year_mismatch_note)
        self.assertEqual(len(calls), 2)  # year-scoped attempt, then the title-only fallback

    async def test_no_matches_at_all_returns_empty_without_a_technical_failure(self) -> None:
        fetch = make_combined_fetch(search_responses={})
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))

        search = await service.find_candidates("Some Obscure Title", 1978)

        self.assertEqual(search.matches, ())
        self.assertFalse(search.technical_failure)
        self.assertIsNone(search.year_mismatch_note)  # nothing to note -- the fallback found nothing either

    async def test_unknown_year_searches_title_only_once(self) -> None:
        calls: list = []
        fetch = make_combined_fetch(search_responses={("Alien", None): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]}}, calls=calls)
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))

        search = await service.find_candidates("Alien", None)

        self.assertEqual(len(search.matches), 1)
        self.assertEqual(len(calls), 1)

    async def test_a_technical_failure_is_reported_distinctly_from_zero_matches(self) -> None:
        fetch = make_combined_fetch(raise_for_search=True)
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))

        search = await service.find_candidates("Alien", 1979)

        self.assertTrue(search.technical_failure)
        self.assertEqual(search.matches, ())
        self.assertIsNotNone(search.error_message)

    async def test_not_configured_is_a_technical_failure(self) -> None:
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="", fetch_json=lambda u: {}))

        search = await service.find_candidates("Alien", 1979)

        self.assertTrue(search.technical_failure)


class AcceptMatchTests(ImdbMetadataRecoveryServiceTestCase):
    def _match(self) -> ImdbSearchMatch:
        return ImdbSearchMatch(imdb_id="tt0078748", imdb_url="https://www.imdb.com/title/tt0078748/", title="Alien", year="1979", media_type="movie")

    async def test_accepting_a_match_saves_the_identifier_and_refreshes_metadata_immediately(self) -> None:
        item = self._add("Alien")
        fetch = make_combined_fetch(title_responses={"tt0078748": ALIEN_PAYLOAD})
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))

        outcome = await service.accept_match(item, self._match(), fetch_cache={})

        self.assertEqual(outcome.result, RecoveryResult.MATCHED)
        updated = self.suggestion_service.get_suggestion(item.id)
        self.assertEqual(updated.director, "Ridley Scott")
        self.assertEqual(updated.imdb_rating, "8.5")
        self.assertIn("Sigourney Weaver", updated.cast)

    async def test_never_overwrites_an_existing_imdb_identifier(self) -> None:
        item = self._add("Alien", imdb_url="https://www.imdb.com/title/tt0000001/")
        fetch = make_combined_fetch(title_responses={"tt0078748": ALIEN_PAYLOAD})
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))

        outcome = await service.accept_match(item, self._match(), fetch_cache={})

        self.assertEqual(outcome.result, RecoveryResult.FAILED)
        unchanged = self.suggestion_service.get_suggestion(item.id)
        self.assertEqual(unchanged.metadata_ids[MetadataProvider.IMDB], "https://www.imdb.com/title/tt0000001/")

    async def test_a_shared_fetch_cache_is_reused_across_two_accepted_matches(self) -> None:
        first = self._add("Alien")
        second = self._add("Alien Again")
        fetch_calls: list = []
        fetch = make_combined_fetch(title_responses={"tt0078748": ALIEN_PAYLOAD}, calls=fetch_calls)
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))
        shared_cache: dict = {}

        await service.accept_match(first, self._match(), fetch_cache=shared_cache)
        await service.accept_match(second, self._match(), fetch_cache=shared_cache)

        title_lookup_calls = [call for call in fetch_calls if "i=tt0078748" in call]
        self.assertEqual(len(title_lookup_calls), 1)

    async def test_enrichment_failure_after_saving_the_identifier_is_still_matched(self) -> None:
        item = self._add("Alien")
        fetch = make_combined_fetch(title_responses={})  # tt0078748 resolves to "Incorrect IMDb ID."
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))

        outcome = await service.accept_match(item, self._match(), fetch_cache={})

        self.assertEqual(outcome.result, RecoveryResult.MATCHED)
        self.assertIn("future Refresh IMDb Metadata run", outcome.detail)
        saved = self.suggestion_service.get_suggestion(item.id)
        self.assertTrue(any(value == "https://www.imdb.com/title/tt0078748/" for value in saved.metadata_ids.values()))

    async def test_post_sync_is_invoked_only_after_metadata_is_persisted(self) -> None:
        item = self._add("Alien")
        fetch = make_combined_fetch(title_responses={"tt0078748": ALIEN_PAYLOAD})
        service = ImdbMetadataRecoveryService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))
        seen_directors: list = []

        async def post_sync(watch_item):
            seen_directors.append(watch_item.director)
            return True

        outcome = await service.accept_match(item, self._match(), fetch_cache={}, post_sync=post_sync)

        self.assertEqual(seen_directors, ["Ridley Scott"])
        self.assertTrue(outcome.post_synced)


if __name__ == "__main__":
    unittest.main()
