"""The small amount of genuinely new logic /browse needs: a richer per-
suggestion text line (title, year, IMDb rating, MPAA rating, genres,
suggester, status, reference) than /list's own build_suggestion_entry_line
provides, and filter-state revalidation when the browsed collection
changes.

Everything else /browse does -- collection resolution, eligibility,
the five shared filters (Genre/IMDb Rating/MPAA Rating/Actor/Member),
pagination, Random Pick, Start Vote -- is orchestration of already-
existing services and bot.py handlers (CollectionEligibilityService,
services/nominee_pool_filter.py, filter_menu_view.py,
services/random_watch_service.py, pagination_view.py, the Custom Vote
flow); see bot.py's browse handlers. Deliberately Discord-free, like
services/random_watch_service.py, so both functions here are testable
without any discord.py object.
"""

from __future__ import annotations

from typing import List, Sequence

from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.services.nominee_pool_filter import (
    ActorFilter,
    GenreFilter,
    MemberSuggestionFilter,
    MpaaRatingFilter,
)
from watch_party_manager.services.suggestion_display_status import (
    SuggestionDisplayStatus,
    format_display_status_with_won_date,
)
from watch_party_manager.services.title_formatter import format_title_with_year


def build_browse_entry_line(item: WatchItem, display_status: SuggestionDisplayStatus) -> str:
    """Render one /browse result: title, year, reference, then a details
    line (IMDb rating, MPAA rating, genres -- whichever are actually
    stored) and a suggester/status line.

    Reuses WatchItem.reference (already the stable, zero-padded "#0007"
    form) and format_display_status_with_won_date -- the same status
    presentation (its own emoji plus label, and a Vote Winner/Watched
    date when recorded) the public confirmation embed and /edit_suggestion
    already use -- rather than reformatting status a second, different
    way (e.g. a second, redundant leading emoji /list's own single-emoji
    line doesn't need since it shows nothing else about status).
    """
    heading = format_title_with_year(item.title, item.release_year)
    line = f"**{heading}** -- {item.reference}"

    details: List[str] = []
    if item.imdb_rating:
        details.append(f"IMDb {item.imdb_rating}/10")
    if item.content_rating:
        details.append(item.content_rating)
    if item.genres:
        details.append(", ".join(item.genres))
    if details:
        line += "\n" + " • ".join(details)

    suggested_by = f"<@{item.journey.original_suggester}>" if item.journey.original_suggester else "Unknown"
    line += f"\nSuggested by {suggested_by} • {format_display_status_with_won_date(item, display_status)}"

    return line


def revalidate_browse_filter_state(filter_state: dict, eligible_pool: Sequence[WatchItem]) -> dict:
    """Section 4/12: when the browsed collection changes, clear only the
    filters that would no longer match anything in the new collection's
    eligible pool -- everything else stays exactly as set.

    Reuses each filter's own .apply() purely as a "would this match
    anything here?" presence check, rather than reimplementing any
    genre/rating/actor/member comparison logic -- no new filtering rules
    are introduced by this function. IMDb Rating is deliberately never
    cleared: a numeric range isn't tied to per-collection enumerated
    values the way Genre/MPAA Rating/Actor/Member are, so it's never
    structurally "invalid" just because the collection changed.

    Returns a new dict (the caller's existing filter_state is left
    untouched) -- collection changes always start a fresh filter-menu
    session anyway (see bot.py's send_browse_session), so there is no
    stale-reference concern from returning a new object here.
    """
    revalidated = dict(filter_state)
    if revalidated["genre"] and not GenreFilter(genre=revalidated["genre"]).apply(eligible_pool):
        revalidated["genre"] = None
    if revalidated["mpaa_rating"] and not MpaaRatingFilter(rating=revalidated["mpaa_rating"]).apply(eligible_pool):
        revalidated["mpaa_rating"] = None
    if revalidated["actor"] and not ActorFilter(actor=revalidated["actor"]).apply(eligible_pool):
        revalidated["actor"] = None
    if revalidated["member_id"] is not None and not MemberSuggestionFilter(
        discord_user_id=revalidated["member_id"], member_display=revalidated["member_display"]
    ).apply(eligible_pool):
        revalidated["member_id"] = None
        revalidated["member_display"] = None
    return revalidated


__all__ = ["build_browse_entry_line", "revalidate_browse_filter_state"]
