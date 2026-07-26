"""WASH's single shared helper for displaying a collection's name.

Every user-facing surface that shows a collection (voting posts and
announcements, `/list`, `/database_list`, suggestion confirmation posts,
`/config`) builds its display through format_collection_display() so a
collection's presentation -- name, plus a built-in emoji for the
standard collection types -- can never drift between surfaces or
duplicate the emoji-matching logic.
"""

from __future__ import annotations

import re
from typing import Optional

# Ordered (pattern, emoji) pairs for WASH's built-in "standard collection"
# types. Matched as a whole word against the collection's current display
# name (case-insensitive) -- e.g. "Movie Suggestions" and "Movies" both
# match the "movie" keyword, but a genuinely custom name like "Book Club
# Adaptations" matches nothing and is shown as-is (Requirement 2: never
# guess an emoji for a custom collection). The first match wins; these
# six keywords don't overlap in practice.
_COLLECTION_EMOJI_KEYWORDS: tuple[tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"\bmovies?\b", re.IGNORECASE), "🎬"),
    (re.compile(r"\btv\b", re.IGNORECASE), "📺"),
    (re.compile(r"\banime\b", re.IGNORECASE), "🎌"),
    (re.compile(r"\bholidays?\b", re.IGNORECASE), "🎄"),
    (re.compile(r"\bdocumentar(?:y|ies)\b", re.IGNORECASE), "🎞️"),
    (re.compile(r"\bhorror\b", re.IGNORECASE), "🎃"),
)


def collection_emoji(display_name: str) -> Optional[str]:
    """Return the built-in emoji for a standard collection name.

    Args:
        display_name: The collection's current display name (see
            bot.py's _resolve_collection_name).

    Returns:
        The matching emoji, or None if the name doesn't match any
        recognized standard-collection keyword -- meaning it's a custom
        collection and should be shown with no emoji at all.
    """
    for pattern, emoji in _COLLECTION_EMOJI_KEYWORDS:
        if pattern.search(display_name):
            return emoji
    return None


def format_collection_display(display_name: str) -> str:
    """Build a collection's one consistent user-facing display.

    Requirement 3: the single shared helper every UI surface must use --
    "Emoji + Display Name" for a recognized standard collection (e.g.
    "🎬 Movie Suggestions"), or just "Display Name" for a custom one.
    """
    emoji = collection_emoji(display_name)
    return f"{emoji} {display_name}" if emoji else display_name
