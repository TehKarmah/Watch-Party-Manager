"""Unit tests for services/imdb_metadata_refresh_service.py -- the pure,
Discord-free core of IMDb Metadata Refresh: scope resolution (guild
isolation), eligibility, deduplicated fetching, merge behavior, and
progress/summary aggregation. Discord-specific concerns (the manage-menu
entry point, scope/confirmation screens, post-sync, progress message
edits) are covered separately in test_imdb_metadata_refresh_command.py.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.imdb_metadata_refresh_service import (
    FETCH_MAX_ATTEMPTS,
    ImdbMetadataRefreshService,
    SuggestionRefreshResult,
    resolve_refreshable_databases,
)
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_A = 100
GUILD_B = 200
ALIEN_URL = "https://www.imdb.com/title/tt0078748/"
PREDATOR_URL = "https://www.imdb.com/title/tt0093773/"

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


def make_fetch(responses: dict, *, calls: list | None = None, raise_for: set | None = None):
    """Build a fetch_json for ImdbMetadataService: `responses` maps a
    request URL substring (the imdb_id) to a payload dict; `calls`
    (if given) records every URL requested; `raise_for` (if given) is a
    set of imdb_ids that raise once per call (used to simulate a
    transient failure a retry then recovers from, or a permanent one).
    """
    calls = calls if calls is not None else []

    def _fetch(url: str):
        calls.append(url)
        for imdb_id, payload in responses.items():
            if imdb_id in url:
                if raise_for and imdb_id in raise_for:
                    raise_for.discard(imdb_id)  # only raises once, unless caller re-adds it
                    raise RuntimeError("simulated transient network failure")
                return payload
        return {"Response": "False", "Error": "Incorrect IMDb ID."}

    return _fetch


class ImdbMetadataRefreshServiceTestCase(unittest.IsolatedAsyncioTestCase):
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


class ResolveRefreshableDatabasesTests(unittest.TestCase):
    def test_filters_to_the_current_guild_only(self) -> None:
        this_guild = SuggestionDatabase(database_id=1, name="A", guild_id=GUILD_A, channel_id=10)
        other_guild = SuggestionDatabase(database_id=2, name="B", guild_id=GUILD_B, channel_id=20)

        result = resolve_refreshable_databases([this_guild, other_guild], guild_id=GUILD_A)

        self.assertEqual(result, [this_guild])

    def test_excludes_inactive_collections(self) -> None:
        active = SuggestionDatabase(database_id=1, name="Active", guild_id=GUILD_A, channel_id=10, active=True)
        inactive = SuggestionDatabase(database_id=2, name="Inactive", guild_id=GUILD_A, channel_id=20, active=False)

        result = resolve_refreshable_databases([active, inactive], guild_id=GUILD_A)

        self.assertEqual(result, [active])

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(resolve_refreshable_databases([], guild_id=GUILD_A), [])


class EligibilityTests(ImdbMetadataRefreshServiceTestCase):
    def test_usable_imdb_identifier_is_recognized(self) -> None:
        item = self._add("Alien", imdb_url=ALIEN_URL)
        service = ImdbMetadataRefreshService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=lambda u: {}))

        self.assertIsNotNone(service.has_usable_imdb_identifier(item))

    def test_missing_imdb_identifier_is_not_usable(self) -> None:
        item = self._add("Untitled")
        service = ImdbMetadataRefreshService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=lambda u: {}))

        self.assertIsNone(service.has_usable_imdb_identifier(item))

    def test_count_refreshable_makes_no_network_request(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        self._add("Untitled")
        calls: list = []
        service = ImdbMetadataRefreshService(
            self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=make_fetch({}, calls=calls))
        )

        usable, total = service.count_refreshable(self.database.database_id)

        self.assertEqual((usable, total), (1, 2))
        self.assertEqual(calls, [])

    def test_archived_suggestions_are_still_considered(self) -> None:
        item = self._add("Alien", imdb_url=ALIEN_URL)
        self.suggestion_service.archive_suggestion(item.id)

        service = ImdbMetadataRefreshService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=lambda u: {}))
        usable, total = service.count_refreshable(self.database.database_id)

        self.assertEqual((usable, total), (1, 1))


class RefreshOutcomeTests(ImdbMetadataRefreshServiceTestCase):
    async def test_valid_imdb_id_is_refreshed(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD})),
        )

        summary = await service.refresh_databases([self.database])

        self.assertEqual(summary.refreshed, 1)
        self.assertEqual(summary.processed, 1)

    async def test_missing_imdb_id_is_skipped(self) -> None:
        self._add("Untitled")
        service = ImdbMetadataRefreshService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=lambda u: {}))

        summary = await service.refresh_databases([self.database])

        self.assertEqual(summary.skipped, 1)
        self.assertEqual(summary.refreshed, 0)

    async def test_duplicate_imdb_ids_in_the_same_collection_are_fetched_once_and_each_evaluated_independently(
        self,
    ) -> None:
        # Two distinct suggestions that happen to share one IMDb ID
        # within the SAME collection are an edge case worth its own
        # test: since a refreshed title always becomes the identical
        # canonical "Title (Year)" string for both, the second one to
        # apply inevitably collides with the first under this
        # collection's title-uniqueness rule (see
        # SuggestionService.apply_imdb_metadata_refresh) -- that must be
        # reported as Failed for the colliding record specifically,
        # never silently merged, never corrupting the first (already
        # successfully refreshed) record, and the lookup itself must
        # still only happen once regardless.
        self._add("Alien", imdb_url=ALIEN_URL)
        self._add("Alien Again", imdb_url=ALIEN_URL)
        calls: list = []
        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD}, calls=calls)),
        )

        summary = await service.refresh_databases([self.database])

        self.assertEqual(len(calls), 1)  # deduplicated: one lookup, reused for both
        self.assertEqual(summary.refreshed, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.processed, 2)
        # The first record was successfully refreshed and is untouched
        # by the second one's failed attempt.
        titles = {item.title for item in self.suggestion_service.get_suggestions_for_database(self.database.database_id)}
        self.assertIn("Alien (1979)", titles)
        self.assertIn("Alien Again", titles)  # unchanged since its own update was refused

    async def test_permanent_lookup_failure_is_reported_as_failed(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        service = ImdbMetadataRefreshService(
            self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=lambda u: {"Response": "False", "Error": "Incorrect IMDb ID."})
        )

        summary = await service.refresh_databases([self.database])

        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.refreshed, 0)

    async def test_transient_failure_recovers_after_one_retry(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        calls: list = []
        raise_for = {"tt0078748"}
        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(
                api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD}, calls=calls, raise_for=raise_for)
            ),
        )

        summary = await service.refresh_databases([self.database])

        self.assertEqual(len(calls), 2)  # one failed attempt, one successful retry
        self.assertEqual(summary.refreshed, 1)
        self.assertEqual(summary.failed, 0)

    async def test_failure_is_never_retried_more_than_the_bounded_budget(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        calls: list = []

        def always_fail(url: str):
            calls.append(url)
            raise RuntimeError("simulated permanent network failure")

        service = ImdbMetadataRefreshService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=always_fail))

        summary = await service.refresh_databases([self.database])

        self.assertEqual(len(calls), FETCH_MAX_ATTEMPTS)
        self.assertEqual(summary.failed, 1)

    async def test_malformed_response_counts_as_failed(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        service = ImdbMetadataRefreshService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=lambda u: "not-json-and-not-a-dict"))

        summary = await service.refresh_databases([self.database])

        self.assertEqual(summary.failed, 1)

    async def test_processing_continues_after_an_individual_failure(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        self._add("Predator", imdb_url=PREDATOR_URL)

        def fetch(url: str):
            if "tt0078748" in url:
                raise RuntimeError("boom")
            if "tt0093773" in url:
                return {
                    "Response": "True",
                    "Title": "Predator",
                    "Year": "1987",
                    "Genre": "Action",
                    "Rated": "R",
                    "imdbRating": "7.8",
                }
            return {"Response": "False", "Error": "Incorrect IMDb ID."}

        service = ImdbMetadataRefreshService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))

        summary = await service.refresh_databases([self.database])

        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.refreshed, 1)
        self.assertEqual(summary.processed, 2)


class MergeBehaviorTests(ImdbMetadataRefreshServiceTestCase):
    async def test_changed_metadata_is_refreshed_and_persisted(self) -> None:
        item = self._add("Alien", imdb_url=ALIEN_URL)
        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD})),
        )

        await service.refresh_databases([self.database])

        updated = self.suggestion_service.get_suggestion(item.id)
        self.assertEqual(updated.cast, ("Sigourney Weaver", "Tom Skerritt"))
        self.assertEqual(updated.director, "Ridley Scott")
        self.assertEqual(updated.imdb_rating, "8.5")
        self.assertEqual(updated.content_rating, "R")
        self.assertEqual(updated.genres, ("Horror", "Sci-Fi"))
        self.assertEqual(updated.runtime_minutes, 117)
        self.assertEqual(updated.poster_url, "https://example.com/alien.jpg")
        self.assertEqual(updated.description, "A crew finds a hostile alien.")
        self.assertEqual(updated.title, "Alien (1979)")

    async def test_identical_metadata_is_reported_unchanged_and_not_repersisted(self) -> None:
        item = self.suggestion_service.suggest(
            "Alien (1979)",
            imdb_url=ALIEN_URL,
            database_id=self.database.database_id,
            guild_id=GUILD_A,
            genres=("Horror", "Sci-Fi"),
            runtime_minutes=117,
            content_rating="R",
            director="Ridley Scott",
            imdb_rating="8.5",
            poster_url="https://example.com/alien.jpg",
            description="A crew finds a hostile alien.",
            cast=("Sigourney Weaver", "Tom Skerritt"),
        ).watch_item
        before = self.suggestion_service.get_suggestion(item.id)

        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD})),
        )
        summary = await service.refresh_databases([self.database])

        self.assertEqual(summary.unchanged, 1)
        self.assertEqual(summary.refreshed, 0)
        after = self.suggestion_service.get_suggestion(item.id)
        self.assertEqual(before.updated_at, after.updated_at)  # never re-persisted

    async def test_provider_omitting_a_field_preserves_the_existing_valid_value(self) -> None:
        item = self.suggestion_service.suggest(
            "Alien",
            imdb_url=ALIEN_URL,
            database_id=self.database.database_id,
            guild_id=GUILD_A,
            director="Someone Manually Entered",
        ).watch_item
        sparse_payload = {"Response": "True", "Title": "Alien", "Year": "1979"}  # no Director field at all
        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": sparse_payload})),
        )

        await service.refresh_databases([self.database])

        updated = self.suggestion_service.get_suggestion(item.id)
        self.assertEqual(updated.director, "Someone Manually Entered")

    async def test_cast_backfill_for_a_suggestion_added_before_cast_was_captured(self) -> None:
        item = self._add("Alien", imdb_url=ALIEN_URL)
        self.assertEqual(item.cast, ())
        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD})),
        )

        summary = await service.refresh_databases([self.database])

        self.assertEqual(summary.refreshed, 1)
        updated = self.suggestion_service.get_suggestion(item.id)
        self.assertEqual(updated.cast, ("Sigourney Weaver", "Tom Skerritt"))

    async def test_rating_backfill(self) -> None:
        item = self._add("Alien", imdb_url=ALIEN_URL)
        self.assertIsNone(item.imdb_rating)
        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD})),
        )

        await service.refresh_databases([self.database])

        self.assertEqual(self.suggestion_service.get_suggestion(item.id).imdb_rating, "8.5")

    async def test_mpaa_content_rating_backfill(self) -> None:
        item = self._add("Alien", imdb_url=ALIEN_URL)
        self.assertIsNone(item.content_rating)
        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD})),
        )

        await service.refresh_databases([self.database])

        self.assertEqual(self.suggestion_service.get_suggestion(item.id).content_rating, "R")

    async def test_title_and_year_are_combined_into_the_canonical_title(self) -> None:
        item = self._add("alien (old title)", imdb_url=ALIEN_URL)
        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD})),
        )

        await service.refresh_databases([self.database])

        self.assertEqual(self.suggestion_service.get_suggestion(item.id).title, "Alien (1979)")

    async def test_all_wash_owned_fields_are_preserved(self) -> None:
        item = self.suggestion_service.suggest(
            "Alien",
            imdb_url=ALIEN_URL,
            database_id=self.database.database_id,
            guild_id=GUILD_A,
            channel_id=555,
            original_suggester="12345",
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(item.id, GUILD_A, 555, 999)
        self.suggestion_service.set_suggestion_status(item.id, item.status)  # no-op, exercises status path
        before = self.suggestion_service.get_suggestion(item.id)

        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD})),
        )
        await service.refresh_databases([self.database])

        after = self.suggestion_service.get_suggestion(item.id)
        self.assertEqual(after.id, before.id)
        self.assertEqual(after.database_id, before.database_id)
        self.assertEqual(after.guild_id, before.guild_id)
        self.assertEqual(after.channel_id, before.channel_id)
        self.assertEqual(after.message_id, before.message_id)
        self.assertEqual(after.status, before.status)
        self.assertEqual(after.journey, before.journey)
        self.assertEqual(after.release_year, before.release_year)

    async def test_persistence_failure_is_isolated_to_the_offending_suggestion(self) -> None:
        # A title collision within the same collection makes
        # apply_imdb_metadata_refresh refuse the update -- confirm this
        # is reported as Failed for that suggestion only, and any other
        # suggestion in the same run still refreshes normally.
        self._add("Alien (1979)", imdb_url=None)  # occupies the exact title the refresh would produce
        colliding = self._add("Alien", imdb_url=ALIEN_URL)
        self._add("Predator", imdb_url=PREDATOR_URL)

        def fetch(url: str):
            if "tt0078748" in url:
                return ALIEN_PAYLOAD
            if "tt0093773" in url:
                return {"Response": "True", "Title": "Predator", "Year": "1987"}
            return {"Response": "False", "Error": "Incorrect IMDb ID."}

        service = ImdbMetadataRefreshService(self.suggestion_service, ImdbMetadataService(api_key="k", fetch_json=fetch))
        summary = await service.refresh_databases([self.database])

        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.refreshed, 1)
        # The colliding suggestion is untouched.
        self.assertEqual(self.suggestion_service.get_suggestion(colliding.id).title, "Alien")


class ProgressAndPostSyncTests(ImdbMetadataRefreshServiceTestCase):
    async def test_progress_is_reported_per_suggestion_and_at_collection_boundaries(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        self._add("Predator", imdb_url=PREDATOR_URL)
        events = []

        async def on_progress(progress):
            events.append((progress.suggestions_processed, progress.collection_boundary))

        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(
                api_key="k",
                fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD, "tt0093773": {"Response": "True", "Title": "Predator", "Year": "1987"}}),
            ),
        )

        await service.refresh_databases([self.database], on_progress=on_progress)

        # Two per-suggestion events, then one collection-boundary event.
        self.assertEqual(events, [(1, False), (2, False), (2, True)])

    async def test_post_sync_is_only_attempted_for_refreshed_suggestions(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)  # will refresh
        self._add("Untitled")  # will skip -- no post_sync call expected

        synced_for = []

        async def post_sync(watch_item):
            synced_for.append(watch_item.id)
            return True

        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD})),
        )
        summary = await service.refresh_databases([self.database], post_sync=post_sync)

        self.assertEqual(len(synced_for), 1)
        self.assertEqual(summary.posts_updated, 1)
        self.assertEqual(summary.post_sync_failures, 0)

    async def test_post_sync_failure_is_recorded_but_does_not_change_the_refresh_result(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)

        async def post_sync(watch_item):
            return False

        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD})),
        )
        summary = await service.refresh_databases([self.database], post_sync=post_sync)

        self.assertEqual(summary.refreshed, 1)  # metadata update itself still counts as refreshed
        self.assertEqual(summary.post_sync_failures, 1)
        self.assertEqual(summary.posts_updated, 0)

    async def test_post_sync_returning_none_is_not_counted_as_a_failure(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)

        async def post_sync(watch_item):
            return None  # nothing to sync -- not a failure

        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD})),
        )
        summary = await service.refresh_databases([self.database], post_sync=post_sync)

        self.assertEqual(summary.post_sync_failures, 0)
        self.assertEqual(summary.posts_updated, 0)


class MultiCollectionAndIdempotencyTests(ImdbMetadataRefreshServiceTestCase):
    async def test_multiple_collections_are_each_processed_with_their_own_totals(self) -> None:
        second_database = self.suggestion_service.create_database("TV Shows", guild_id=GUILD_A, channel_id=20).database
        self._add("Alien", imdb_url=ALIEN_URL)
        self._add("Predator", imdb_url=PREDATOR_URL, database_id=second_database.database_id)

        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(
                api_key="k",
                fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD, "tt0093773": {"Response": "True", "Title": "Predator", "Year": "1987"}}),
            ),
        )

        summary = await service.refresh_databases([self.database, second_database])

        self.assertEqual(summary.collections_processed, 2)
        self.assertEqual(summary.refreshed, 2)
        self.assertEqual([c.database_name for c in summary.collections], ["Movies", "TV Shows"])
        self.assertEqual(summary.collections[0].refreshed, 1)
        self.assertEqual(summary.collections[1].refreshed, 1)

    async def test_a_title_shared_across_two_collections_is_still_fetched_only_once(self) -> None:
        second_database = self.suggestion_service.create_database("TV Shows", guild_id=GUILD_A, channel_id=20).database
        self._add("Alien", imdb_url=ALIEN_URL)
        self._add("Alien", imdb_url=ALIEN_URL, database_id=second_database.database_id)
        calls: list = []

        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD}, calls=calls)),
        )

        summary = await service.refresh_databases([self.database, second_database])

        self.assertEqual(len(calls), 1)
        self.assertEqual(summary.refreshed, 2)

    async def test_rerunning_after_a_full_refresh_is_idempotent(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(api_key="k", fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD})),
        )

        first = await service.refresh_databases([self.database])
        second = await service.refresh_databases([self.database])

        self.assertEqual(first.refreshed, 1)
        self.assertEqual(second.refreshed, 0)
        self.assertEqual(second.unchanged, 1)
        # No duplicate records were created.
        self.assertEqual(len(self.suggestion_service.get_suggestions_for_database(self.database.database_id)), 1)

    async def test_rerun_after_partial_completion_only_reprocesses_what_remains_to_change(self) -> None:
        # Simulates "the bot stopped mid-run": one suggestion was already
        # refreshed in a prior run, another was not reached yet.
        self._add("Alien", imdb_url=ALIEN_URL)
        self._add("Predator", imdb_url=PREDATOR_URL)
        service = ImdbMetadataRefreshService(
            self.suggestion_service,
            ImdbMetadataService(
                api_key="k",
                fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD, "tt0093773": {"Response": "True", "Title": "Predator", "Year": "1987"}}),
            ),
        )
        # First "run" only ever reaches Alien (simulating an interruption).
        await service._refresh_one(self.suggestion_service.get_suggestions_for_database(self.database.database_id)[0], {}, None)

        summary = await service.refresh_databases([self.database])

        self.assertEqual(summary.refreshed, 1)  # Predator, newly refreshed
        self.assertEqual(summary.unchanged, 1)  # Alien, already up to date from the "interrupted" run


if __name__ == "__main__":
    unittest.main()
