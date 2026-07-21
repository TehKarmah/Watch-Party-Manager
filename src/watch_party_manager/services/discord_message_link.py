"""Discord message jump-link formatting.

A single canonical place for the "https://discord.com/channels/..." URL
format, so it's never hand-built more than once per feature. Lives
alongside discord_timestamp_formatter.py for the same reason: it needs to
be reachable from both bot.py and the scheduler package without either
importing the other (bot.py already imports from scheduler, so the
reverse would be circular).
"""

from __future__ import annotations


def build_discord_message_link(guild_id: int, channel_id: int, message_id: int) -> str:
    """Build a jump link to a specific Discord message.

    Args:
        guild_id: The Discord guild the message was posted in.
        channel_id: The Discord channel or thread the message was posted in.
        message_id: The message's ID.

    Returns:
        A URL that opens directly to the message in Discord.
    """
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
