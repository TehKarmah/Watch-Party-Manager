"""Shared helper for rendering an hour-based voting duration in natural language.

A standalone module (mirroring title_formatter.py's existing pattern) so
bot.py, setup_wizard_service.py, config_service.py, and
vote_announcement_formatter.py can all share one implementation without
importing each other.
"""

from __future__ import annotations

HOURS_PER_DAY = 24

MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = MINUTES_PER_HOUR * 24
MINUTES_PER_WEEK = MINUTES_PER_DAY * 7


def format_duration_hours(hours: int) -> str:
    """Render an hour count as natural language: whole days when evenly
    divisible by 24 (e.g. "3 days"), otherwise hours (e.g. "4 hours").

    This is the single consistent convention used everywhere a voting
    duration is displayed -- Setup Wizard/`/config` summaries,
    `/start_vote` confirmations, and voting round announcements -- so a
    72-hour duration always reads as "3 days", never as the more awkward
    "72 hours".
    """
    if hours % HOURS_PER_DAY == 0 and hours >= HOURS_PER_DAY:
        days = hours // HOURS_PER_DAY
        return f"{days} day" if days == 1 else f"{days} days"
    return f"{hours} hour" if hours == 1 else f"{hours} hours"


def format_duration_minutes(minutes: int) -> str:
    """Render a minute count as natural language, picking the largest
    whole unit it evenly divides into -- weeks, then days, then hours,
    falling back to minutes (e.g. "10 minutes", "1 hour", "12 hours",
    "1 day", "1 week"). One level more granular than format_duration_hours,
    for reminder-before-close and /edit_vote's Shorten/Extend Vote
    (Requirement 3: one shared duration syntax; Requirement 4: minute
    precision for reminders).
    """
    if minutes % MINUTES_PER_WEEK == 0 and minutes >= MINUTES_PER_WEEK:
        weeks = minutes // MINUTES_PER_WEEK
        return f"{weeks} week" if weeks == 1 else f"{weeks} weeks"
    if minutes % MINUTES_PER_DAY == 0 and minutes >= MINUTES_PER_DAY:
        days = minutes // MINUTES_PER_DAY
        return f"{days} day" if days == 1 else f"{days} days"
    if minutes % MINUTES_PER_HOUR == 0 and minutes >= MINUTES_PER_HOUR:
        hours = minutes // MINUTES_PER_HOUR
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
