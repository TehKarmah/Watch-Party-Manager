"""Finalizes a completed voting round's Discord presentation.

FR-026 requires the completion presentation to be identical no matter how
a round completed -- automatically (CloseVoteJobHandler, in the scheduler
package) or manually via /edit_vote's "End Now" action (bot.py). This
module is the single place that presentation is produced and delivered,
so both callers share it directly instead of maintaining two versions
that could drift apart.

Lives in services/, alongside vote_announcement_formatter.py, for the
same reason that module does: reachable from scheduler job handlers
without either importing bot.py, which would create a circular import
(bot.py already imports from the scheduler package).

Scheduling concerns (cancelling a round's pending close_vote/vote_reminder
jobs) are deliberately NOT handled here -- that stays with each caller,
since it's a scheduling decision, not a presentation one, and the two
existing completion paths already handle it differently (an automatic
close is itself the job being completed; "End Now" cancels the
now-redundant jobs before calling this module).
"""

from __future__ import annotations

import logging
from typing import List, Optional, Protocol

from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.services.discord_message_link import build_discord_message_link
from watch_party_manager.services.vote_announcement_formatter import (
    build_closed_voting_post_text,
    build_vote_completion_announcement,
    build_vote_link,
    build_vote_results_embeds,
)
from watch_party_manager.services.vote_completion_service import VoteCompletionResult

logger = logging.getLogger(__name__)


class SuggestionLookup(Protocol):
    """The subset of SuggestionService needed to resolve a round's
    candidates and winner(s) to their WatchItems.

    Kept minimal and Protocol-based, matching the project's existing
    dependency pattern (see WinningSuggestionLookup in
    close_vote_job_handler.py), so this module depends only on the one
    capability it actually uses.
    """

    def get_suggestion(self, suggestion_id: int) -> Optional[WatchItem]: ...


class ResultsMessageRecorder(Protocol):
    """The subset of VoteService needed to persist the results
    announcement's message reference.
    """

    def attach_results_message_reference(self, round_id: int, message_id: int) -> bool: ...


class DiscordChannelMessenger(Protocol):
    """Duck-typed subset of a discord.Client/Bot this module needs.

    Mirrors scheduler.job_handler.DiscordChannelMessenger's exact shape
    without importing it, so services/ never depends on the scheduler
    package -- the same reasoning WinningSuggestionLookup already
    applies to SuggestionService.
    """

    def get_channel(self, channel_id: int): ...

    async def fetch_channel(self, channel_id: int): ...


def _resolve_watch_items(suggestion_service: SuggestionLookup, suggestion_ids: List[int]) -> List[WatchItem]:
    """Resolve suggestion IDs to their current WatchItems, skipping any
    that no longer exist (e.g. removed after the round closed).
    """
    resolved: List[WatchItem] = []
    for suggestion_id in suggestion_ids:
        watch_item = suggestion_service.get_suggestion(suggestion_id)
        if watch_item is not None:
            resolved.append(watch_item)
    return resolved


async def _resolve_channel(messenger: DiscordChannelMessenger, channel_id: int):
    channel = messenger.get_channel(channel_id)
    if channel is None:
        channel = await messenger.fetch_channel(channel_id)
    return channel


async def _update_original_voting_post(messenger: DiscordChannelMessenger, vote_round, content: str) -> None:
    """Best-effort update of the round's original voting post.

    Mirrors bot.py's update_voting_message() exactly (fetch, edit,
    swallow any Discord-side failure) -- duplicated here rather than
    imported, since bot.py cannot be imported from a module reachable by
    the scheduler package. Always clears the message's view: once a
    round has completed, its voting buttons must never remain usable.
    Also always explicitly clears any embed: while open, the post is
    WASH's active-vote embed (Release Polish Batch 2, Priority 5), and
    Discord's edit() leaves an omitted embed untouched rather than
    removing it, so an explicit embed=None is required here to replace
    it with this plain-text closed record rather than showing both.
    """
    if vote_round.channel_id is None or vote_round.message_id is None:
        return

    try:
        channel = await _resolve_channel(messenger, vote_round.channel_id)
        message = await channel.fetch_message(vote_round.message_id)
        await message.edit(content=content, embed=None, view=None)
    except Exception:
        logger.exception(
            "Could not update the original voting message for round %s", vote_round.id
        )


async def finalize_vote_completion(
    vote_service: ResultsMessageRecorder,
    suggestion_service: SuggestionLookup,
    messenger: DiscordChannelMessenger,
    result: VoteCompletionResult,
) -> None:
    """Present a just-completed voting round: update the original post,
    disable its buttons, and post the single canonical results announcement.

    Order of operations (see FR-026's Completion Flow):
      1. Update the original voting post to show it's closed, with the
         winner(s) and final standings already visible, and its buttons
         disabled -- done before the announcement is sent so the
         historical record is accurate even if sending the announcement
         itself fails.
      2. Post the results announcement (text + "About Tonight's Pick"
         embed(s)) to the round's channel -- the single new public
         message this produces.
      3. Persist the announcement's message reference via
         attach_results_message_reference().
      4. Edit the original post again to include a jump link to the
         results announcement now that it exists.

    Safe to call only once per completed round: VoteCompletionService.
    complete_round() already returns None for a round that's no longer
    OPEN, so neither existing caller (CloseVoteJobHandler, /edit_vote's
    "End Now") ever reaches this function twice for the same round --
    this function does not need its own separate duplicate-announcement
    guard.

    If the round has no channel reference at all, nothing is posted or
    edited (logged and returned) -- mirrors CloseVoteJobHandler's
    pre-FR-026 behavior for that case.

    Args:
        vote_service: Used to persist the results announcement's message reference.
        suggestion_service: Used to resolve candidates and winner(s) to their WatchItems.
        messenger: Used to resolve the round's channel and send/edit messages.
        result: The just-completed round's outcome.
    """
    vote_round = result.vote_round

    if vote_round.channel_id is None:
        logger.warning(
            "Voting round %s completed but has no channel reference; results announcement not sent",
            vote_round.id,
        )
        return

    candidates = _resolve_watch_items(suggestion_service, vote_round.candidate_suggestion_ids)
    winning_items = _resolve_watch_items(suggestion_service, result.winning_suggestion_ids)

    closed_text = build_closed_voting_post_text(
        vote_round, candidates, winning_items, result.standings, result.total_votes_cast
    )
    await _update_original_voting_post(messenger, vote_round, closed_text)

    try:
        channel = await _resolve_channel(messenger, vote_round.channel_id)
    except Exception:
        logger.exception(
            "Could not resolve the channel for voting round %s; results announcement not sent",
            vote_round.id,
        )
        return

    original_vote_link = build_vote_link(vote_round)
    announcement_text = build_vote_completion_announcement(
        vote_round, candidates, winning_items, result.standings, result.total_votes_cast, original_vote_link
    )
    embeds = build_vote_results_embeds(winning_items, result.standings)

    try:
        sent_message = await channel.send(content=announcement_text, embeds=embeds)
    except Exception:
        logger.exception("Could not send the results announcement for voting round %s", vote_round.id)
        return

    logger.info("Posted results announcement for voting round %s", vote_round.id)
    vote_service.attach_results_message_reference(vote_round.id, sent_message.id)

    if vote_round.guild_id is not None:
        results_link = build_discord_message_link(vote_round.guild_id, vote_round.channel_id, sent_message.id)
        closed_text_with_link = build_closed_voting_post_text(
            vote_round,
            candidates,
            winning_items,
            result.standings,
            result.total_votes_cast,
            results_link,
        )
        await _update_original_voting_post(messenger, vote_round, closed_text_with_link)
