"""WASH's single shared duration-text parser.

Every feature that accepts a relative duration from a WASH Crew member or
Watch Party member -- vote duration, reminder-before-close, and
/edit_vote's Shorten Vote/Extend Vote -- parses that text through
parse_duration_to_minutes() so the accepted syntax can never drift
between features. Returns whole minutes, the one unit every caller
shares (Release Candidate Polish: Vote Duration removed vote duration's
former whole-hour-only restriction, so it now supports the same minute
precision as reminders and Shorten/Extend Vote).
"""

from __future__ import annotations

import re

_DURATION_PATTERN = re.compile(
    r"^(\d+)\s*(m|minute|minutes|h|hour|hours|d|day|days|w|week|weeks)$",
    re.IGNORECASE,
)

_UNIT_TO_MINUTES = {
    "m": 1,
    "minute": 1,
    "minutes": 1,
    "h": 60,
    "hour": 60,
    "hours": 60,
    "d": 60 * 24,
    "day": 60 * 24,
    "days": 60 * 24,
    "w": 60 * 24 * 7,
    "week": 60 * 24 * 7,
    "weeks": 60 * 24 * 7,
}

DURATION_SYNTAX_HELP = (
    "Duration must be a whole number immediately followed by a unit -- "
    "m/minutes, h/hours, d/days, or w/weeks (e.g. '10m', '1h', '1d', '1w')."
)


def parse_duration_to_minutes(text: str) -> int:
    """Parse a relative duration into whole minutes.

    Args:
        text: The raw duration text, e.g. "10m", "1h", "12 hours", "1w".

    Returns:
        The equivalent whole number of minutes.

    Raises:
        ValueError: If text isn't a positive whole number immediately
            followed by one of the recognized units.
    """
    cleaned = (text or "").strip()
    match = _DURATION_PATTERN.match(cleaned)
    if not match:
        raise ValueError(DURATION_SYNTAX_HELP)
    amount = int(match.group(1))
    unit = match.group(2).lower()
    return amount * _UNIT_TO_MINUTES[unit]
