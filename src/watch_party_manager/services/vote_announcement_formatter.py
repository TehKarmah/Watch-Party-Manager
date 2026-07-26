"""Builds the user-facing text and embeds for a voting round's lifecycle messages.

Lives outside bot.py, alongside discord_timestamp_formatter.py, so it can
be reused by scheduler job handlers (e.g. CloseVoteJobHandler) and by the
shared vote_completion_announcer module without those modules importing
bot.py, which would create a circular import (bot.py already imports from
the scheduler package). Building a discord.Embed here is still pure,
side-effect-free presentation -- no network or bot state is touched --
so it stays consistent with this module's existing "formatting only"
contract.
"""

from __future__ import annotations

from typing import List, Optional, Protocol

import discord

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.vote import VoteRound, VoteVisibility
from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.services.collection_display import format_collection_display
from watch_party_manager.services.discord_message_link import build_discord_message_link
from watch_party_manager.services.discord_timestamp_formatter import format_datetime_for_display
from watch_party_manager.services.title_formatter import format_title_with_year
from watch_party_manager.services.vote_service import StandingsEntry

# Matches build_suggestion_confirmation_embed's own accent color in bot.py,
# so a winning suggestion's embed reads as the same kind of "record card"
# whether it's shown at suggestion time or at vote-completion time.
WINNER_EMBED_COLOR = 0xF5C518


class DatabaseLookup(Protocol):
    """The subset of SuggestionService needed to resolve a voting round's
    collection to a display name. Kept minimal and Protocol-based,
    matching this project's existing dependency conventions.
    """

    def get_database(self, database_id: int) -> Optional[SuggestionDatabase]: ...


def resolve_vote_collection_name(
    suggestion_service: DatabaseLookup, database_id: Optional[int]
) -> Optional[str]:
    """Resolve a voting round's collection to a display name, shared by
    every vote-lifecycle message builder (Requirement 1: voting is
    centered on the collection, not the round number) so the name is
    resolved identically everywhere a round is presented.

    Returns None when the round predates collection association
    (database_id is None) or the collection no longer exists -- callers
    fall back to a round-centric title in that case (see
    format_vote_title).
    """
    if database_id is None:
        return None
    database = suggestion_service.get_database(database_id)
    return database.name if database is not None else None


def format_vote_title(collection_name: Optional[str], suffix: str) -> str:
    """Build the collection-centric headline for a vote-related message,
    e.g. "🎬 Movie Suggestions Voting — Closed" (Requirement 1). `suffix`
    is expected to already read naturally on its own (e.g. "Voting Is
    Open", "Voting — Closed") -- that's exactly the fallback used when no
    collection context is available (e.g. a legacy round that predates
    collection association, or its collection was since removed). The
    round number remains available separately as secondary information
    (see build_vote_round_line) in every case.
    """
    if collection_name:
        return f"{format_collection_display(collection_name)} {suffix}"
    return suffix


def build_vote_round_line(vote_round: VoteRound) -> str:
    """Build the round-number line shown as secondary information
    beneath every collection-centric vote title (Requirement 1).
    """
    return f"Round: {vote_round.id}"


def format_standings_lines(
    standings: Optional[List[StandingsEntry]],
    standings_error: Optional[str],
    candidates: Optional[List[WatchItem]] = None,
) -> List[str]:
    """Build the display lines for a round's standings, if any are shown.

    Shared by /vote_status and /vote (for visible rounds) so the standings
    format stays identical and isn't duplicated per call site. Not used
    for a completed round's own presentation -- see
    build_final_standings_lines() for that, which additionally includes
    every nominee (not just those with at least one vote) and links each
    title to its original suggestion.

    Args:
        standings: Standings entries to display, or None to show nothing.
        standings_error: A message to show instead of standings if
            calculating them failed, or None.
        candidates: The round's nominees, used to resolve each entry's
            suggestion_id to its title (Release Polish Batch 2, Priority
            3 -- standings must show titles, not internal suggestion
            numbers). Optional so existing callers with no candidates in
            scope keep working; a candidate missing from this list (or
            candidates itself being None) falls back to the bare
            "Suggestion #N" label rather than failing the command.

    Returns:
        Lines to append to a message, starting with a blank separator
        line. Empty if there's nothing to show (both args are None).
    """
    if standings_error is not None:
        return ["", f"Standings unavailable: {standings_error}"]

    if standings is not None:
        if not standings:
            return ["", "Standings: no votes yet."]
        candidates_by_id = {candidate.id: candidate for candidate in candidates or []}
        lines = ["", "Standings:"]
        for entry in standings:
            vote_word = "vote" if entry.vote_count == 1 else "votes"
            candidate = candidates_by_id.get(entry.suggestion_id)
            label = (
                _format_candidate_title(candidate)
                if candidate is not None
                else f"Suggestion #{entry.suggestion_id}"
            )
            lines.append(f"{label} — {entry.vote_count} {vote_word}")
        return lines

    return []


def build_vote_link(vote_round: VoteRound) -> Optional[str]:
    """Build a jump link to a round's original voting post, when known.

    Returns None for a round missing one or more of guild_id/channel_id/
    message_id -- e.g. a legacy round created before message references
    existed, or one whose reference was never attached. Every caller that
    shows this link treats None the same way: omit the link entirely
    rather than showing a broken one.
    """
    if vote_round.guild_id is None or vote_round.channel_id is None or vote_round.message_id is None:
        return None
    return build_discord_message_link(vote_round.guild_id, vote_round.channel_id, vote_round.message_id)


def build_suggestion_link(watch_item: WatchItem) -> Optional[str]:
    """Build a jump link to a suggestion's original post, when known.

    Mirrors build_vote_link's exact "omit gracefully" contract, applied
    to a suggestion's own guild_id/channel_id/message_id (see FR-024)
    rather than a vote round's. Returns None for a legacy suggestion
    missing one or more of those fields, so callers can fall back to
    plain (unlinked) text. Relocated here from bot.py in FR-026 so it can
    be reused by vote_completion_announcer.py (and therefore by
    CloseVoteJobHandler) without importing bot.py.
    """
    if watch_item.guild_id is None or watch_item.channel_id is None or watch_item.message_id is None:
        return None
    return build_discord_message_link(watch_item.guild_id, watch_item.channel_id, watch_item.message_id)


def build_vote_deadline_change_notice(vote_round: VoteRound, collection_name: Optional[str] = None) -> str:
    """Build the public notice announcing a round's deadline changed.

    Posted by /edit_vote's "Change End Time" action (including Shorten
    Vote/Extend Vote), after VoteService.reschedule_round() has already
    updated and persisted the round's new closes_at.

    Args:
        vote_round: The round, already updated to its new closes_at.
        collection_name: The round's collection, if known (Requirement
            1) -- see resolve_vote_collection_name. None falls back to a
            generic, round-centric title.

    Returns:
        The notice text, including a link to the original post when available.
    """
    lines = [
        f"**{format_vote_title(collection_name, 'Voting — Updated')}**",
        build_vote_round_line(vote_round),
        f"Voting now ends: {format_datetime_for_display(vote_round.closes_at)}",
    ]
    link = build_vote_link(vote_round)
    if link:
        lines.append(f"Original post: {link}")
    return "\n".join(lines)


def build_vote_cancellation_notice(vote_round: VoteRound, collection_name: Optional[str] = None) -> str:
    """Build the public notice announcing a round was cancelled.

    Posted by /edit_vote's "Cancel Vote" action, after
    VoteService.cancel_round() has already marked the round CANCELLED.
    Never mentions a winner -- cancelling a vote never determines one.

    Args:
        vote_round: The now-cancelled round.
        collection_name: The round's collection, if known (Requirement
            1) -- see resolve_vote_collection_name. None falls back to a
            generic, round-centric title.

    Returns:
        The notice text, including a link to the original post when available.
    """
    lines = [
        f"**{format_vote_title(collection_name, 'Voting — Cancelled')}**",
        build_vote_round_line(vote_round),
        "Cancelled by WASH Crew.",
    ]
    link = build_vote_link(vote_round)
    if link:
        lines.append(f"Original post: {link}")
    return "\n".join(lines)


def _format_candidate_title(watch_item: WatchItem) -> str:
    """Format one candidate's title for display, linked to its original
    suggestion message when available. Never links to IMDb (FR-026's
    Message Links requirement). Shows the release year exactly once --
    see format_title_with_year -- whether or not it was already embedded
    in the title by IMDb resolution.
    """
    display_title = format_title_with_year(watch_item.title, watch_item.release_year)
    link = build_suggestion_link(watch_item)
    if link:
        return f"[{display_title}]({link})"
    return display_title


def build_final_standings_lines(
    candidates: List[WatchItem], standings: Optional[List[StandingsEntry]]
) -> List[str]:
    """Build the "Final Standings" lines for a just-completed voting round.

    Every nominee is shown, not just those who received a vote --
    VoteService.calculate_standings() only returns entries for
    suggestions with at least one vote, so any candidate absent from it
    is appended afterward with an explicit zero count, in its original
    candidate (button) order. standings is already sorted by vote count
    descending, then suggestion ID ascending (see calculate_standings),
    which is exactly "winners first, ties broken deterministically" --
    that ordering is reused unchanged, never recomputed here.

    Args:
        candidates: Every nominee in the round, in button order.
        standings: The final vote tally, or None/empty if nobody voted.

    Returns:
        One line per candidate, e.g. "Brazil (1985) — 4 votes" (no
        leading nominee number -- Release Polish Batch 2, Priority 4), in
        placement order. Empty if there are no candidates to show.
    """
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    lines: List[str] = []
    shown_ids: set[int] = set()

    for entry in standings or []:
        watch_item = candidates_by_id.get(entry.suggestion_id)
        if watch_item is None:
            continue
        vote_word = "vote" if entry.vote_count == 1 else "votes"
        lines.append(f"{_format_candidate_title(watch_item)} — {entry.vote_count} {vote_word}")
        shown_ids.add(entry.suggestion_id)

    for candidate in candidates:
        if candidate.id in shown_ids:
            continue
        lines.append(f"{_format_candidate_title(candidate)} — 0 votes")

    return lines


def build_vote_reminder_standings_lines(
    vote_round: VoteRound, candidates: List[WatchItem], standings: Optional[List[StandingsEntry]]
) -> List[str]:
    """Build the "Current standings" section for a pre-close vote reminder.

    FR-027: preserves the project's existing blind-vote visibility rule
    -- a blind round never reveals standings while still open (see
    bot.py's build_candidate_standings_lines, which applies the identical
    rule to the voting post itself). This is that same rule, reused here
    rather than reimplemented, since a reminder fires while the round is
    still open and visibility still matters (unlike
    build_final_standings_lines, used only after a round has closed,
    when standings are always safe to reveal regardless of visibility).

    Args:
        vote_round: The still-open round the reminder is for.
        candidates: Every nominee in the round, in button order.
        standings: The current vote tally, or None/empty if nobody has
            voted yet.

    Returns:
        Lines to append to the reminder, starting with a blank separator line.
    """
    if vote_round.visibility != VoteVisibility.VISIBLE:
        return ["", "Votes hidden until voting closes."]

    standings_lines = build_final_standings_lines(candidates, standings)
    if not standings_lines:
        return ["", "Current standings: no votes yet."]
    return ["", "Current standings:", *standings_lines]


def _build_winner_summary_line(winning_items: List[WatchItem], total_votes_cast: int) -> str:
    """Build the single line announcing the winner(s), or the lack of any.

    Branches on total_votes_cast -- the round's authoritative vote count
    -- rather than on whether winning_items is empty. Those are NOT the
    same condition: winning_items can also come back empty when votes
    were cast but the winning suggestion(s) could no longer be resolved
    (e.g. removed after the round closed), and that must never be
    reported as "no votes were cast" (see FR-026's bug fix).
    """
    if total_votes_cast == 0:
        return "No votes were cast, so no winner could be determined."
    if not winning_items:
        return "Votes were cast, but the winning suggestion(s) could not be found."
    if len(winning_items) == 1:
        return f"Winner: {_format_candidate_title(winning_items[0])}"
    winners_display = ", ".join(_format_candidate_title(item) for item in winning_items)
    return f"It's a tie! Winners: {winners_display}"


def _build_final_standings_block(
    candidates: List[WatchItem], standings: Optional[List[StandingsEntry]]
) -> List[str]:
    """Build the blank-line-prefixed "Final Standings:" block, or nothing."""
    standings_lines = build_final_standings_lines(candidates, standings)
    if not standings_lines:
        return []
    return ["", "Final Standings:", *standings_lines]


def _build_suggested_by_line(winning_items: List[WatchItem]) -> Optional[str]:
    """Build the "Suggested by:" line naming who originally submitted the
    winning suggestion(s).

    Reuses WatchItemJourney.original_suggester -- already recorded at
    suggestion time (see SuggestionService.suggest) -- so this never
    performs a new lookup, only formats what's already on the WatchItem.
    Mirrors _build_winner_summary_line's tie handling: one mention per
    tied winner, in the same order, deduplicated if the same member
    suggested more than one tied winner. Omitted entirely (returns None)
    when there's no winner to attribute, or none of the winner(s) have a
    recorded suggester (e.g. a suggestion created before FR-034 began
    tracking one) -- matching this module's existing "omit gracefully
    rather than show a placeholder" convention for optional lines (see
    build_vote_link's callers).
    """
    suggester_ids: List[str] = []
    for item in winning_items:
        suggester_id = item.journey.original_suggester
        if suggester_id and suggester_id not in suggester_ids:
            suggester_ids.append(suggester_id)
    if not suggester_ids:
        return None
    mentions = ", ".join(f"<@{suggester_id}>" for suggester_id in suggester_ids)
    return f"Suggested by: {mentions}"


def build_vote_completion_announcement(
    vote_round: VoteRound,
    candidates: List[WatchItem],
    winning_items: List[WatchItem],
    standings: Optional[List[StandingsEntry]],
    total_votes_cast: int,
    original_vote_link: Optional[str] = None,
    collection_name: Optional[str] = None,
) -> str:
    """Build the single canonical results announcement for a just-completed round.

    This is FR-026's "Results" section: which collection the round
    belonged to, winner(s), final standings for every nominee, who
    suggested the winner, and a link back to the original voting post.
    The "About the Winner" section (poster, runtime, rating, genres,
    summary) is a separate discord.Embed built by build_vote_results_embeds()
    and sent alongside this text in the same message -- see
    vote_completion_announcer.finalize_vote_completion(), the single
    place both are combined and posted, so every completion path
    (automatic or /edit_vote "End Now") produces an identical announcement.

    Args:
        vote_round: The round that just completed.
        candidates: Every nominee in the round, in button order -- used
            so Final Standings can show every nominee, not just those
            who received a vote.
        winning_items: The winning suggestion(s)' WatchItems, in the same
            order as vote_round's winner calculation. Empty if nobody
            voted, or if the winning suggestion(s) could no longer be
            resolved.
        standings: The final vote tally, reused from
            VoteService.calculate_standings() rather than reformatted here.
        total_votes_cast: How many members voted in this round -- the
            authoritative source for whether any votes were cast at all
            (see _build_winner_summary_line).
        original_vote_link: A jump link to the original voting post, or
            None to omit it (e.g. missing message metadata).
        collection_name: The collection this round belongs to, already
            resolved by the caller (see resolve_vote_collection_name),
            or None for a legacy round created before rounds recorded
            their collection -- falls back to a generic, round-centric
            title (Requirement 1).

    Returns:
        The announcement text.
    """
    lines = [
        f"**{format_vote_title(collection_name, 'Voting — Results')}**",
        build_vote_round_line(vote_round),
    ]
    lines.append(_build_winner_summary_line(winning_items, total_votes_cast))
    lines.append(f"Total votes cast: {total_votes_cast}")
    lines.extend(_build_final_standings_block(candidates, standings))

    suggested_by_line = _build_suggested_by_line(winning_items)
    if suggested_by_line:
        lines.append("")
        lines.append(suggested_by_line)

    if original_vote_link:
        lines.append("")
        lines.append(f"Original voting post: {original_vote_link}")

    if winning_items:
        lines.append("")
        lines.append("**About the Winner**")

    return "\n".join(lines)


def build_closed_voting_post_text(
    vote_round: VoteRound,
    candidates: List[WatchItem],
    winning_items: List[WatchItem],
    standings: Optional[List[StandingsEntry]],
    total_votes_cast: int,
    results_link: Optional[str] = None,
    collection_name: Optional[str] = None,
) -> str:
    """Build the original voting post's text once the round has completed.

    Replaces its "is open!" text and interactive buttons (buttons are
    cleared by the caller, see vote_completion_announcer.py) with a
    closed record showing the winner(s) and final standings, so the
    original post remains an accurate historical record even though it's
    no longer interactive.

    Args:
        vote_round: The round that just completed.
        candidates: Every nominee, in button order (see
            build_final_standings_lines).
        winning_items: The winning suggestion(s)' WatchItems, as in
            build_vote_completion_announcement.
        standings: The final vote tally.
        total_votes_cast: How many members voted in this round.
        results_link: A jump link to the results announcement, or None
            to omit it -- e.g. before the announcement has been sent
            yet (this text is built once immediately on close, then
            again with this link once the announcement exists).
        collection_name: The round's collection, if known (Requirement
            1) -- see resolve_vote_collection_name. None falls back to a
            generic, round-centric title.

    Returns:
        The updated original-post text.
    """
    lines = [
        f"**{format_vote_title(collection_name, 'Voting — Closed')}**",
        build_vote_round_line(vote_round),
        _build_winner_summary_line(winning_items, total_votes_cast),
        f"Total votes cast: {total_votes_cast}",
    ]
    lines.extend(_build_final_standings_block(candidates, standings))

    if results_link:
        lines.append("")
        lines.append(f"Results announcement: {results_link}")

    return "\n".join(lines)


def build_winner_detail_embed(watch_item: WatchItem, vote_count: int, *, show_thumbnail: bool) -> discord.Embed:
    """Build the "About the Winner" embed for one winning suggestion.

    The embed's title links to the suggestion's original message when
    available -- never to IMDb (FR-026's Message Links requirement).

    Args:
        watch_item: The winning suggestion to display.
        vote_count: This suggestion's final vote count.
        show_thumbnail: Whether to attach a poster thumbnail. False for
            every winner when there's a tie (FR-026 forbids thumbnails
            for multiple winners); gracefully omitted regardless if no
            poster_url is on file.

    Returns:
        The embed.
    """
    embed = discord.Embed(
        title=watch_item.title,
        description=watch_item.description,
        url=build_suggestion_link(watch_item),
        color=WINNER_EMBED_COLOR,
    )
    if watch_item.runtime_minutes:
        embed.add_field(name="Runtime", value=f"{watch_item.runtime_minutes} min", inline=True)
    if watch_item.imdb_rating:
        embed.add_field(name="IMDb Rating", value=f"{watch_item.imdb_rating}/10", inline=True)
    if watch_item.genres:
        embed.add_field(name="Genres", value=" • ".join(watch_item.genres), inline=True)
    vote_word = "vote" if vote_count == 1 else "votes"
    embed.add_field(name="Votes", value=f"{vote_count} {vote_word}", inline=True)
    if show_thumbnail and watch_item.poster_url:
        embed.set_thumbnail(url=watch_item.poster_url)
    return embed


def build_vote_results_embeds(
    winning_items: List[WatchItem], standings: Optional[List[StandingsEntry]]
) -> List[discord.Embed]:
    """Build the "About the Winner" embed(s) for a completed round's winner(s).

    One embed per winning item. A thumbnail is only ever included when
    there is exactly one winner -- FR-026 explicitly forbids thumbnails
    when multiple winners tie.

    Args:
        winning_items: The winning suggestion(s)' WatchItems.
        standings: The final vote tally, used to show each winner's own
            vote count (all tied winners share the same count, but each
            is looked up individually rather than assumed).

    Returns:
        One embed per winning item, in the same order. Empty if there
        are no winning items (no votes cast, or unresolvable winners).
    """
    if not winning_items:
        return []

    vote_counts_by_suggestion_id = {entry.suggestion_id: entry.vote_count for entry in (standings or [])}
    show_thumbnail = len(winning_items) == 1
    return [
        build_winner_detail_embed(
            item, vote_counts_by_suggestion_id.get(item.id, 0), show_thumbnail=show_thumbnail
        )
        for item in winning_items
    ]
