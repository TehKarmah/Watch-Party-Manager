"""Builds the user-facing text for a voting round's lifecycle messages.

Lives outside bot.py, alongside discord_timestamp_formatter.py, so it can
be reused by scheduler job handlers (e.g. CloseVoteJobHandler) without
those modules importing bot.py, which would create a circular import
(bot.py already imports from the scheduler package).
"""

from __future__ import annotations

from typing import List, Optional

from watch_party_manager.domain.vote import VoteRound
from watch_party_manager.domain.watch_item import MetadataProvider, WatchItem
from watch_party_manager.services.discord_message_link import build_discord_message_link
from watch_party_manager.services.discord_timestamp_formatter import format_datetime_for_display
from watch_party_manager.services.vote_service import StandingsEntry


def format_standings_lines(
    standings: Optional[List[StandingsEntry]],
    standings_error: Optional[str],
) -> List[str]:
    """Build the display lines for a round's standings, if any are shown.

    Shared by /vote_status, /vote (for visible rounds), and the completion
    announcement so the standings format stays identical and isn't
    duplicated per call site.

    Args:
        standings: Standings entries to display, or None to show nothing.
        standings_error: A message to show instead of standings if
            calculating them failed, or None.

    Returns:
        Lines to append to a message, starting with a blank separator
        line. Empty if there's nothing to show (both args are None).
    """
    if standings_error is not None:
        return ["", f"Standings unavailable: {standings_error}"]

    if standings is not None:
        if not standings:
            return ["", "Standings: no votes yet."]
        lines = ["", "Standings:"]
        for position, entry in enumerate(standings, start=1):
            vote_word = "vote" if entry.vote_count == 1 else "votes"
            lines.append(f"{position}. Suggestion #{entry.suggestion_id} — {entry.vote_count} {vote_word}")
        return lines

    return []


def build_vote_link(vote_round: VoteRound) -> Optional[str]:
    """Build a jump link to a round's original voting post, when known.

    Returns None for a round missing one or more of guild_id/channel_id/
    message_id -- e.g. a legacy round created before message references
    existed, or one whose reference was never attached. Every caller that
    shows this link (/vote_status, /vote confirmations, vote reminders,
    and the deadline-change/cancellation notices below) treats None the
    same way: omit the link entirely rather than showing a broken one.
    """
    if vote_round.guild_id is None or vote_round.channel_id is None or vote_round.message_id is None:
        return None
    return build_discord_message_link(vote_round.guild_id, vote_round.channel_id, vote_round.message_id)


def build_vote_deadline_change_notice(vote_round: VoteRound) -> str:
    """Build the public notice announcing a round's deadline changed.

    Posted by /edit_vote's "Change End Time" action, after
    VoteService.reschedule_round() has already updated and persisted the
    round's new closes_at.

    Args:
        vote_round: The round, already updated to its new closes_at.

    Returns:
        The notice text, including a link to the original post when available.
    """
    lines = [
        f"Voting round {vote_round.id}'s deadline has changed.",
        f"Voting now ends: {format_datetime_for_display(vote_round.closes_at)}",
    ]
    link = build_vote_link(vote_round)
    if link:
        lines.append(f"Original post: {link}")
    return "\n".join(lines)


def build_vote_cancellation_notice(vote_round: VoteRound) -> str:
    """Build the public notice announcing a round was cancelled.

    Posted by /edit_vote's "Cancel Vote" action, after
    VoteService.cancel_round() has already marked the round CANCELLED.
    Never mentions a winner -- cancelling a vote never determines one.

    Args:
        vote_round: The now-cancelled round.

    Returns:
        The notice text, including a link to the original post when available.
    """
    lines = [f"Voting round {vote_round.id} has been cancelled by WASH Crew."]
    link = build_vote_link(vote_round)
    if link:
        lines.append(f"Original post: {link}")
    return "\n".join(lines)


def _format_winner_display(watch_item: WatchItem) -> str:
    """Format one winner for the announcement: title, plus an IMDb link
    when the winning suggestion already has one on file (see
    build_suggestion_confirmation_embed in bot.py for the same
    "[View on IMDb](url)" convention used at suggestion time).
    """
    imdb_url = watch_item.metadata_ids.get(MetadataProvider.IMDB)
    if imdb_url:
        return f"{watch_item.title} ([View on IMDb]({imdb_url}))"
    return watch_item.title


def build_vote_completion_announcement(
    vote_round: VoteRound,
    winning_items: List[WatchItem],
    standings: Optional[List[StandingsEntry]],
    total_votes_cast: int,
) -> str:
    """Build the public announcement for a just-completed voting round.

    By the time this is called the round is already closed, so standings
    are always safe to reveal here -- including for a round that was
    blind while open. That's the entire mechanism behind "reveal standings
    only after voting has closed" for blind rounds: this function is only
    ever invoked post-closure, so there's no separate blind-vs-visible
    branch needed here the way build_voting_post_text has one for the
    still-open case.

    Args:
        vote_round: The round that just completed.
        winning_items: The winning suggestion(s)' WatchItems, in the same
            order as vote_round's winner calculation. Empty if nobody
            voted. Each is shown by title, with an IMDb link appended
            when one is already on file.
        standings: The final vote tally, reused from
            VoteService.calculate_standings() via format_standings_lines
            rather than reformatted here.
        total_votes_cast: How many members voted in this round.

    Returns:
        The announcement text.
    """
    lines = [f"Voting round {vote_round.id} has closed!"]

    if not winning_items:
        lines.append("No votes were cast, so no winner could be determined.")
    elif len(winning_items) == 1:
        lines.append(f"Winner: {_format_winner_display(winning_items[0])}")
    else:
        winners_display = ", ".join(_format_winner_display(item) for item in winning_items)
        lines.append(f"It's a tie! Winners: {winners_display}")

    lines.append(f"Total votes cast: {total_votes_cast}")
    lines.extend(format_standings_lines(standings, None))

    return "\n".join(lines)
