"""Discord UI components for the New Review Workflow's Crew Review
notification: the Admin Channel post shown once a suggestion's "I Won't
Watch" rejection count reaches its configured threshold.

Mirrors membership_view.py's design: this module has no dependency on
bot.py. CrewReviewView only knows how to render itself (a stable
Retire/Keep Active/Reset Rejections/View Suggestion button row keyed to
one suggestion_id) and forward a click to a caller-supplied callback --
all decision logic lives in services/suggestion_service.py and bot.py's
wiring around it.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

import discord

OnCrewReviewDecision = Callable[[discord.Interaction, int], Awaitable[None]]


def build_retire_button_custom_id(suggestion_id: int) -> str:
    """Build the stable custom_id for one suggestion's Crew Review Retire button."""
    return f"wpm_crew_review_retire_{suggestion_id}"


def build_keep_active_button_custom_id(suggestion_id: int) -> str:
    """Build the stable custom_id for one suggestion's Crew Review Keep Active button."""
    return f"wpm_crew_review_keep_active_{suggestion_id}"


def build_reset_rejections_button_custom_id(suggestion_id: int) -> str:
    """Build the stable custom_id for one suggestion's Crew Review Reset Rejections button."""
    return f"wpm_crew_review_reset_rejections_{suggestion_id}"


class RetireCrewReviewButton(discord.ui.Button):
    def __init__(self, suggestion_id: int, on_retire: OnCrewReviewDecision) -> None:
        super().__init__(
            label="Retire",
            style=discord.ButtonStyle.danger,
            custom_id=build_retire_button_custom_id(suggestion_id),
        )
        self.suggestion_id = suggestion_id
        self._on_retire = on_retire

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_retire(interaction, self.suggestion_id)


class KeepActiveCrewReviewButton(discord.ui.Button):
    def __init__(self, suggestion_id: int, on_keep_active: OnCrewReviewDecision) -> None:
        super().__init__(
            label="Keep Active",
            style=discord.ButtonStyle.success,
            custom_id=build_keep_active_button_custom_id(suggestion_id),
        )
        self.suggestion_id = suggestion_id
        self._on_keep_active = on_keep_active

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_keep_active(interaction, self.suggestion_id)


class ResetRejectionsCrewReviewButton(discord.ui.Button):
    def __init__(self, suggestion_id: int, on_reset_rejections: OnCrewReviewDecision) -> None:
        super().__init__(
            label="Reset Rejections",
            style=discord.ButtonStyle.secondary,
            custom_id=build_reset_rejections_button_custom_id(suggestion_id),
        )
        self.suggestion_id = suggestion_id
        self._on_reset_rejections = on_reset_rejections

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_reset_rejections(interaction, self.suggestion_id)


class ViewSuggestionLinkButton(discord.ui.Button):
    """Links back to the suggestion's original public post.

    A link-style button: Discord opens the URL client-side without
    firing an interaction, so this needs no callback and no permission
    check of its own.
    """

    def __init__(self, url: str) -> None:
        super().__init__(label="View Suggestion", style=discord.ButtonStyle.link, url=url)


class CrewReviewView(discord.ui.View):
    """The Retire/Keep Active/Reset Rejections/View Suggestion button row
    for one suggestion pending Crew Review.

    timeout=None and stable custom_ids make this a persistent Discord
    view, matching SuggestionView/MembershipApprovalView's own pattern.
    View Suggestion is omitted when the suggestion has no known public
    post to link back to (see bot.py's build_suggestion_message_link) --
    Retire/Keep Active/Reset Rejections remain fully usable either way.
    """

    def __init__(
        self,
        suggestion_id: int,
        on_retire: OnCrewReviewDecision,
        on_keep_active: OnCrewReviewDecision,
        on_reset_rejections: OnCrewReviewDecision,
        *,
        suggestion_url: Optional[str] = None,
    ) -> None:
        if suggestion_id <= 0:
            raise ValueError("CrewReviewView requires a suggestion with a positive ID.")

        super().__init__(timeout=None)
        self.add_item(RetireCrewReviewButton(suggestion_id, on_retire))
        self.add_item(KeepActiveCrewReviewButton(suggestion_id, on_keep_active))
        self.add_item(ResetRejectionsCrewReviewButton(suggestion_id, on_reset_rejections))
        if suggestion_url is not None:
            self.add_item(ViewSuggestionLinkButton(suggestion_url))
