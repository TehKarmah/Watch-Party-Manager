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
# w/week/weeks are deliberately still accepted (never rejected) but never
# advertised anywhere user-facing -- DURATION_SYNTAX_HELP and every UI
# label/placeholder in the project intentionally omit them (Duration UX
# Standard: supported but undocumented).

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
    "m/minutes, h/hours, or d/days (e.g. '10m', '1h', '7d')."
)

# The one canonical set of example values shown wherever WASH names
# sample durations (modal placeholders, pre-modal screen explanations,
# documentation) -- Release UX & Command Surface Cleanup (Vote Duration
# Wording): every caller uses this exact string so examples can never
# drift between features.
DURATION_FORMAT_EXAMPLES = "10m, 1h, 7d"

# A longer, screen-friendly explanation for use immediately before a
# modal opens, where Discord's 45-character TextInput label limit has no
# room to spell out minutes/hours/days on its own (see
# setup_wizard_view.VotingDefaultsModal/ReminderDefaultsModal, whose
# labels now only name the field and its valid range).
DURATION_FORMAT_HELP_TEXT = (
    f"Durations combine a number with a unit -- minutes (m), hours (h), or days (d). "
    f"Examples: {DURATION_FORMAT_EXAMPLES}."
)

# First-Time UX Polish: "vote duration" was observed to read ambiguously
# during a live setup walkthrough -- shown wherever WASH specifically asks
# for *vote* duration (not reminder-before-close or Shorten/Extend Vote,
# which are already unambiguous in their own context) to head off any
# confusion with a movie's own runtime.
VOTE_DURATION_CLARIFICATION = "Vote duration is how long voting stays open -- not the movie's runtime."


def parse_duration_to_minutes(text: str) -> int:
    """Parse a relative duration into whole minutes.

    Args:
        text: The raw duration text, e.g. "10m", "1h", "7d", "12 hours".

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
