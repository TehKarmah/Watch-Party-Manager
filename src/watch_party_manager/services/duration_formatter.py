"""Shared helper for rendering an hour-based voting duration in natural language.

A standalone module (mirroring title_formatter.py's existing pattern) so
bot.py, setup_wizard_service.py, config_service.py, and
vote_announcement_formatter.py can all share one implementation without
importing each other.
"""

from __future__ import annotations

HOURS_PER_DAY = 24


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
