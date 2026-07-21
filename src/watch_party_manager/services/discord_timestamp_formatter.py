"""Discord-native timestamp formatting.

Lives outside bot.py so it can be reused by scheduler job handlers (e.g.
VoteReminderJobHandler) without those modules importing bot.py, which
would create a circular import (bot.py already imports from the
scheduler package).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional


def format_datetime_for_display(value: Optional[datetime]) -> str:
    """Format a datetime using Discord's native timestamp syntax.

    Discord renders native timestamps in each member's local timezone. The
    full timestamp gives the exact date and time, while the relative timestamp
    provides quick context such as "in 7 days."

    Args:
        value: A timezone-aware datetime, or None.

    Returns:
        Discord full and relative timestamp codes, or a fallback message when
        no deadline is set.

    Raises:
        ValueError: If value is a naive datetime.
    """
    if value is None:
        return "No deadline set"
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("value must be timezone-aware")

    unix_timestamp = int(value.timestamp())
    return f"<t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)"
