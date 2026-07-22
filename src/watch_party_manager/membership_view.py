"""Discord UI components for FR-030's Approval-Required join requests.

Mirrors suggestion_view.py's design: this module has no dependency on
bot.py. ApprovalRequestView only knows how to render itself (a stable
Approve/Deny button pair keyed to one request_id) and forward a click to
a caller-supplied callback -- all approval/denial logic lives in
services/membership_service.py and bot.py's wiring around it.

timeout=None and stable custom_ids make this a persistent Discord view,
matching SuggestionView exactly: bot.py re-registers one for every
still-pending request's stored message on startup (see
restore_persistent_membership_approval_views).

Self-Service's leave confirmation does not get a view here -- it reuses
edit_vote_view.py's EditVoteConfirmationView directly (a generic
confirm/abort prompt already established for exactly this kind of
safeguard), so bot.py imports that one instead of a duplicate.
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Tuple

import discord

PENDING_SELECT_VIEW_TIMEOUT_SECONDS = 300

OnMembershipApprovalDecision = Callable[[discord.Interaction, int], Awaitable[None]]
OnPendingRequestSelected = Callable[[discord.Interaction, int], Awaitable[None]]


def build_membership_approve_button_custom_id(request_id: int) -> str:
    """Build the stable custom_id for one request's Approve button.

    Shared between ApproveMembershipRequestButton's construction and any
    future lookup that needs to recognize it (mirrors
    build_reject_button_custom_id's exact role for suggestion buttons).
    """
    return f"wpm_membership_approve_{request_id}"


def build_membership_deny_button_custom_id(request_id: int) -> str:
    """Build the stable custom_id for one request's Deny button."""
    return f"wpm_membership_deny_{request_id}"


class ApproveMembershipRequestButton(discord.ui.Button):
    def __init__(self, request_id: int, on_approve: OnMembershipApprovalDecision) -> None:
        super().__init__(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=build_membership_approve_button_custom_id(request_id),
        )
        self.request_id = request_id
        self._on_approve = on_approve

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_approve(interaction, self.request_id)


class DenyMembershipRequestButton(discord.ui.Button):
    def __init__(self, request_id: int, on_deny: OnMembershipApprovalDecision) -> None:
        super().__init__(
            label="Deny",
            style=discord.ButtonStyle.danger,
            custom_id=build_membership_deny_button_custom_id(request_id),
        )
        self.request_id = request_id
        self._on_deny = on_deny

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_deny(interaction, self.request_id)


class MembershipApprovalView(discord.ui.View):
    """The Approve/Deny button pair WASH Crew sees for one pending request."""

    def __init__(
        self, request_id: int, on_approve: OnMembershipApprovalDecision, on_deny: OnMembershipApprovalDecision
    ) -> None:
        if request_id <= 0:
            raise ValueError("MembershipApprovalView requires a positive request_id.")

        super().__init__(timeout=None)
        self.add_item(ApproveMembershipRequestButton(request_id, on_approve))
        self.add_item(DenyMembershipRequestButton(request_id, on_deny))


# --- FR-031: /watch_party pending's "pick one, then approve/deny" picker -------------------


class PendingRequestSelect(discord.ui.Select):
    """Lets WASH Crew pick one pending request to act on.

    Mirrors ConfigDatabaseSelect/ExistingDatabaseSelect's exact pattern
    (options built from (id, label) pairs, capped at 25) -- reused here
    instead of inventing a new selection style.
    """

    def __init__(self, requests: List[Tuple[int, str]], on_select: OnPendingRequestSelected) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=str(request_id))
            for request_id, label in requests[:25]
        ]
        super().__init__(
            placeholder="Choose a pending request to approve or deny...",
            options=options,
            custom_id="wpm_watch_party_pending_select",
        )
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, int(self.values[0]))


class PendingRequestSelectView(discord.ui.View):
    """/watch_party pending's picker: select a request, then see its
    MembershipApprovalView (reused unchanged, not duplicated) to act on it.
    """

    def __init__(self, requests: List[Tuple[int, str]], on_select: OnPendingRequestSelected) -> None:
        super().__init__(timeout=PENDING_SELECT_VIEW_TIMEOUT_SECONDS)
        self.add_item(PendingRequestSelect(requests, on_select))
