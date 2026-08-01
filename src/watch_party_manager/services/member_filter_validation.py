"""Shared validation for the optional Suggestion Source (member) filter
used by both /vote start's Custom Vote Filters and /random_watch's
filters.

An invalid selection -- not a current Watch Party member, or a current
member with zero eligible suggestions in the resolved collection -- must
never be silently discarded while letting the flow continue as though
Any Member had been chosen. It must block progress until the caller
either picks a valid member or explicitly clears the selection back to
Any Member. This module is the one place that decision gets made, so
the voting flow and /random_watch can never diverge on what counts as a
valid selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, Sequence

from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.services.membership_service import MembershipService
from watch_party_manager.services.nominee_pool_filter import MemberSuggestionFilter


class MemberFilterCandidate(Protocol):
    """Duck-typed subset of a discord.Member this module reads. A bot
    account satisfies this Protocol exactly the same as a human one --
    nothing here inspects `.bot`, so a bot is judged solely on Watch
    Party membership and eligible suggestions, like anyone else.
    """

    id: int
    display_name: str
    roles: List[Any]


@dataclass(frozen=True)
class MemberFilterSelectionResult:
    """The outcome of validating one Suggestion Source selection.

    valid=True with discord_user_id=None represents Any Member (no
    selection was made, or one was explicitly cleared) -- always valid.

    valid=False means the raw selection must never be treated as an
    active filter: never included in a review/result screen, never
    persisted, and never allowed to progress the flow. discord_user_id
    and member_display are always None in this case -- there is no
    silent fallback to a "partial" filter.

    status_line is always safe to display, whether valid or not.
    """

    valid: bool
    discord_user_id: Optional[int]
    member_display: Optional[str]
    status_line: str


def validate_member_filter_selection(
    member: Optional[MemberFilterCandidate],
    *,
    watch_party_role_id: Optional[int],
    eligible_pool: Sequence[WatchItem],
    collection_display_name: str,
) -> MemberFilterSelectionResult:
    """Validate a Suggestion Source selection against both rules every
    caller must enforce: current Watch Party membership, and at least
    one eligible suggestion -- matched by the member's stable Discord
    user ID via MemberSuggestionFilter, never a display name -- in the
    resolved collection.
    """
    if member is None:
        return MemberFilterSelectionResult(valid=True, discord_user_id=None, member_display=None, status_line="")

    is_member = watch_party_role_id is not None and MembershipService.is_current_member(member, watch_party_role_id)
    if not is_member:
        return MemberFilterSelectionResult(
            valid=False,
            discord_user_id=None,
            member_display=None,
            status_line=(
                f"⚠ {member.display_name} is not a current Watch Party member. "
                "Choose another member or clear the member filter."
            ),
        )

    member_filter = MemberSuggestionFilter(discord_user_id=member.id, member_display=member.display_name)
    eligible = member_filter.apply(eligible_pool)
    if not eligible:
        return MemberFilterSelectionResult(
            valid=False,
            discord_user_id=None,
            member_display=None,
            status_line=(
                f"⚠ {member.display_name} has no eligible suggestions in {collection_display_name}. "
                "Choose another member or clear the member filter."
            ),
        )

    suggestion_word = "suggestion" if len(eligible) == 1 else "suggestions"
    return MemberFilterSelectionResult(
        valid=True,
        discord_user_id=member.id,
        member_display=member.display_name,
        status_line=f"{member.display_name} has {len(eligible)} eligible {suggestion_word}.",
    )
