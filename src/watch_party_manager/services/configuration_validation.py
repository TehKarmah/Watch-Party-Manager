"""Shared role/channel validation for FR-028's /setup wizard and FR-029's
/config command.

Both interfaces need to confirm a selected role or channel/thread still
exists (and, for channels, that WASH still has permission to use it)
before persisting a change. Extracted here so the two interfaces validate
identically instead of drifting apart -- neither redesigns how roles or
channels are resolved; both simply ask a live discord.Guild (or an
equivalent fake in tests).
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


class RoleLookup(Protocol):
    """Duck-typed subset of a discord.Guild needed to confirm a role still exists."""

    def get_role(self, role_id: int) -> Optional[Any]: ...


class ChannelLookup(Protocol):
    """Duck-typed subset of a discord.Guild needed to confirm a channel or
    thread still exists and is usable.

    `me` is the bot's own member object in this guild -- used together
    with the returned channel/thread's permissions_for() to validate
    "WASH has sufficient permissions to use each selected resource".
    """

    def get_channel_or_thread(self, channel_id: int) -> Optional[Any]: ...

    @property
    def me(self) -> Any: ...


class GuildLookup(RoleLookup, ChannelLookup, Protocol):
    """Everything this module's validators need from a live Discord guild."""


def validate_role_exists(
    role_id: Optional[int], guild: RoleLookup, *, resource_label: str = "role"
) -> Optional[str]:
    """Confirm a selected role still exists.

    Args:
        role_id: The role to check, or None if nothing was selected (never
            an error -- an unset optional role is a caller-level concern,
            not a validation failure here).
        guild: A live Discord guild (or an equivalent fake in tests).
        resource_label: Used in the returned message, e.g. "WASH Crew role".

    Returns:
        None if role_id is unset or still resolves, otherwise a clear
        error message.
    """
    if role_id is None:
        return None
    if guild.get_role(role_id) is None:
        return f"The selected {resource_label} no longer exists."
    return None


def validate_channel_usable(
    channel_id: Optional[int], guild: ChannelLookup, *, resource_label: str = "channel or thread"
) -> Optional[str]:
    """Confirm a selected channel/thread still exists and WASH can post in it.

    Args:
        channel_id: The channel or thread to check, or None if nothing was
            selected (never an error here).
        guild: A live Discord guild (or an equivalent fake in tests).
        resource_label: Used in the returned message, e.g. "channel or thread".

    Returns:
        None if channel_id is unset or still usable, otherwise a clear
        error message.
    """
    if channel_id is None:
        return None

    channel = guild.get_channel_or_thread(channel_id)
    if channel is None:
        return f"The selected {resource_label} no longer exists."

    permissions = channel.permissions_for(guild.me)
    if not permissions.view_channel or not permissions.send_messages:
        return f"WASH does not have permission to post in the selected {resource_label}."

    return None
