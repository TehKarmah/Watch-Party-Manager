"""Shared role/channel validation for FR-028's /setup wizard, FR-029's
/config command, and FR-030's membership workflow.

These interfaces need to confirm a selected role or channel/thread still
exists (and, for channels, that WASH still has permission to use it)
before persisting a change. Extracted here so they validate identically
instead of drifting apart -- none of them redesign how roles or channels
are resolved; each simply asks a live discord.Guild (or an equivalent
fake in tests).
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


def describe_channel_permission_failure(
    channel: Any, permissions: Any, *, resource_label: str = "channel or thread"
) -> str:
    """A specific, actionable message for a channel/thread WASH can't
    use, naming exactly which permission(s) are missing and what to do
    about it instead of a generic "no permission" message -- never
    suggesting the Administrator permission as a shortcut, and never
    including the raw exception/permission-check details Discord itself
    would show.

    Args:
        channel: The channel/thread WASH can't fully use -- only its
            `id` is read, so any duck-typed object with one works.
        permissions: The result of `channel.permissions_for(guild.me)`.
        resource_label: Names what to choose/create instead, e.g. "Admin
            channel" -> "choose a different Admin channel, or create a
            new one" -- every caller of this function (Admin Channel,
            Home Channel, Watch Destination) already offers its own
            "create a new one" flow, so this is never a dead-end
            suggestion.
    """
    missing = []
    if not permissions.view_channel:
        missing.append("View Channel")
    if not permissions.send_messages:
        missing.append("Send Messages")
    missing_text = " and ".join(missing)
    plural = "s" if len(missing) > 1 else ""
    message = (
        f"WASH cannot send messages in <#{channel.id}>. Grant WASH {missing_text} permission{plural} "
        f"on that channel, choose a different {resource_label}, or create a new one."
    )
    if not permissions.view_channel:
        message += (
            " If it's a private channel, make sure WASH's role has been explicitly added to it -- "
            "and if WASH's role also manages other roles in this server, that role may need to sit "
            "higher in the server's role hierarchy to do so."
        )
    return message


def validate_channel_usable(
    channel_id: Optional[int], guild: ChannelLookup, *, resource_label: str = "channel or thread"
) -> Optional[str]:
    """Confirm a selected channel/thread still exists and WASH can post in it.

    Args:
        channel_id: The channel or thread to check, or None if nothing was
            selected (never an error here).
        guild: A live Discord guild (or an equivalent fake in tests).
        resource_label: Used in the "no longer exists" message, and to
            name what to choose/create instead in the permission-failure
            message (see describe_channel_permission_failure) -- the
            permission-failure message also names the specific channel
            directly, since that's more actionable than a generic label
            alone.

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
        return describe_channel_permission_failure(channel, permissions, resource_label=resource_label)

    return None


def validate_role_mutable(
    role_id: Optional[int], guild: RoleLookup, *, resource_label: str = "role"
) -> Optional[str]:
    """Confirm WASH can actually assign or remove a role, not just reference it.

    FR-030's membership workflow needs this beyond validate_role_exists:
    Discord silently refuses a role change unless the bot both has the
    Manage Roles permission and its own top role sits above the target
    role in the guild's hierarchy.

    Args:
        role_id: The role to check, or None if nothing is configured
            (never an error here -- an unconfigured role is a
            caller-level concern).
        guild: A live Discord guild (or an equivalent fake in tests).
            `guild.me` must additionally expose `guild_permissions` and
            `top_role`, matching a real discord.Member.
        resource_label: Used in the returned message, e.g. "Watch Party role".

    Returns:
        None if role_id is unset or fully usable, otherwise a clear
        error message.
    """
    if role_id is None:
        return None

    role = guild.get_role(role_id)
    if role is None:
        return f"The {resource_label} no longer exists."

    me = guild.me
    if not me.guild_permissions.manage_roles:
        return "WASH does not have the Manage Roles permission."

    if me.top_role.position <= role.position:
        return f"WASH's role must be positioned above the {resource_label} to assign or remove it."

    return None
