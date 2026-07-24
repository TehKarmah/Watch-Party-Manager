"""Shared helper for rendering a watch item's title with its release year.

A standalone module (mirroring discord_message_link.py /
discord_timestamp_formatter.py's existing pattern) so both bot.py and
vote_announcement_formatter.py can share one implementation without
either importing the other -- vote_announcement_formatter.py already
documents why it must never import bot.py (circular import risk).
"""

from __future__ import annotations

from typing import Optional


def format_title_with_year(title: str, release_year: Optional[int]) -> str:
    """Render a watch item's title with its release year shown exactly once.

    OMDb-resolved titles already end with " (YYYY)" (see
    ImdbMetadataService._format_display_title), so unconditionally
    appending release_year would double-print it -- e.g. "50 First Dates
    (2004) (2004)" (Release Polish Batch 2, Priority 2). This only skips
    the append when the title already ends with that *exact* year in
    parentheses; any other trailing parenthetical text (an unrelated
    edition/cut note, or a mismatched year from manual editing) is left
    untouched and the year is still appended, since stripping it would
    risk corrupting a title this function has no way to safely parse.

    The single shared helper for this rendering -- every user-facing
    title display (/list, /vote_status, voting UI, the active-vote
    embed, vote completion announcements) calls this rather than
    reimplementing the same check.
    """
    if release_year is None:
        return title
    year_suffix = f"({release_year})"
    if title.rstrip().endswith(year_suffix):
        return title
    return f"{title} {year_suffix}"
