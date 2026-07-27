"""Derives a suggestion's admin-facing display status.

Replaces the old Watched-based model with four statuses: Available,
Rotation Cooldown, Vote Winner, Retired ("Vote Winner replaces Watched
for v1" -- WASH knows a suggestion won a vote, not that the group
actually watched it).

Only three of those are persisted on WatchItemStatus (SUGGESTED,
VOTE_WINNER, ARCHIVED) -- Rotation Cooldown is deliberately NOT a fourth
enum member. It's computed fresh every time from
RotationService.is_in_rotation_cooldown(), since it must automatically
revert to Available the moment a fresh rotation begins (see that
method's docstring), with no persisted "clear cooldown" step required.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus


class SuggestionDisplayStatus(str, Enum):
    """One of the four statuses shown on a suggestion's embed/listing."""

    AVAILABLE = "available"
    ROTATION_COOLDOWN = "rotation_cooldown"
    VOTE_WINNER = "vote_winner"
    RETIRED = "retired"


SUGGESTION_DISPLAY_STATUS_LABELS: dict[SuggestionDisplayStatus, str] = {
    SuggestionDisplayStatus.AVAILABLE: "🟢 Available",
    SuggestionDisplayStatus.ROTATION_COOLDOWN: "🟡 Rotation Cooldown",
    SuggestionDisplayStatus.VOTE_WINNER: "🟣 Vote Winner",
    SuggestionDisplayStatus.RETIRED: "🔴 Retired",
}


class RotationCooldownLookup:
    """The subset of RotationService this module needs (see Protocol
    convention used throughout services/*.py -- e.g. RotationSuggestionSource).
    """

    def is_in_rotation_cooldown(self, watch_item: WatchItem) -> bool: ...


def compute_display_status(watch_item: WatchItem, *, in_rotation_cooldown: bool) -> SuggestionDisplayStatus:
    """Pure computation: given whether an item is currently on Rotation
    Cooldown, resolve its full display status. Split from
    resolve_display_status() so tests never need a real RotationService
    just to exercise this decision table.
    """
    if watch_item.status is WatchItemStatus.ARCHIVED:
        return SuggestionDisplayStatus.RETIRED
    if watch_item.status is WatchItemStatus.VOTE_WINNER:
        return SuggestionDisplayStatus.VOTE_WINNER
    if in_rotation_cooldown:
        return SuggestionDisplayStatus.ROTATION_COOLDOWN
    return SuggestionDisplayStatus.AVAILABLE


def resolve_display_status(
    watch_item: WatchItem, rotation_service: Optional[RotationCooldownLookup]
) -> SuggestionDisplayStatus:
    """Convenience wrapper: resolve Rotation Cooldown from a real
    RotationService (or skip that check entirely when none is
    configured -- matching every other rotation_service-optional call
    site in bot.py) before delegating to compute_display_status().
    """
    in_rotation_cooldown = (
        rotation_service.is_in_rotation_cooldown(watch_item) if rotation_service is not None else False
    )
    return compute_display_status(watch_item, in_rotation_cooldown=in_rotation_cooldown)


def display_status_label(status: SuggestionDisplayStatus) -> str:
    return SUGGESTION_DISPLAY_STATUS_LABELS[status]
