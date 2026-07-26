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
from dataclasses import dataclass
from typing import Iterable, Optional, Set


@dataclass(frozen=True)
class StandardCollectionType:
    """One of WASH's built-in "standard collection" types.

    key is a stable identifier (never shown to users); button_label and
    default_thread_name back /database add's modernized type-selection
    flow (see bot.py's DatabaseGroup.add) -- keyword_pattern and emoji
    are the same matching this module has always used for display, kept
    as the single source of truth so /database add's "already used"
    check can never disagree with what format_collection_display shows.
    """

    key: str
    button_label: str
    default_thread_name: str
    keyword_pattern: "re.Pattern[str]"
    emoji: str


# Ordered set of WASH's built-in "standard collection" types. Matched as a
# whole word against a collection's current display name (case-
# insensitive) -- e.g. "Movie Suggestions" and "Movies" both match the
# "movie" keyword, but a genuinely custom name like "Book Club
# Adaptations" matches nothing and is shown as-is (Requirement 2: never
# guess an emoji for a custom collection). The first match wins; these
# six keywords don't overlap in practice.
STANDARD_COLLECTION_TYPES: tuple[StandardCollectionType, ...] = (
    StandardCollectionType("movies", "Movies", "Movie Suggestions", re.compile(r"\bmovies?\b", re.IGNORECASE), "🎬"),
    StandardCollectionType("tv_shows", "TV Shows", "TV Suggestions", re.compile(r"\btv\b", re.IGNORECASE), "📺"),
    StandardCollectionType("anime", "Anime", "Anime Suggestions", re.compile(r"\banime\b", re.IGNORECASE), "🎌"),
    StandardCollectionType("holiday", "Holiday", "Holiday Suggestions", re.compile(r"\bholidays?\b", re.IGNORECASE), "🎄"),
    StandardCollectionType(
        "documentaries", "Documentaries", "Documentary Suggestions",
        re.compile(r"\bdocumentar(?:y|ies)\b", re.IGNORECASE), "🎞️",
    ),
    StandardCollectionType("horror", "Horror", "Horror Suggestions", re.compile(r"\bhorror\b", re.IGNORECASE), "🎃"),
)

_COLLECTION_EMOJI_KEYWORDS: tuple[tuple["re.Pattern[str]", str], ...] = tuple(
    (standard_type.keyword_pattern, standard_type.emoji) for standard_type in STANDARD_COLLECTION_TYPES
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


def used_standard_collection_type_keys(collection_names: Iterable[str]) -> Set[str]:
    """Return which STANDARD_COLLECTION_TYPES keys already match at least
    one of the given collection display names.

    Used by /database add's modernized type-selection screen (Command
    Structure Cleanup) to hide a standard type once a collection already
    exists for it -- e.g. once a "Movies"-matching collection exists,
    the type-selection screen no longer offers Movies again. Callers
    pass only active collections' names (matching the same set /list and
    /database list already treat as "this server's collections").
    """
    used: Set[str] = set()
    for name in collection_names:
        for standard_type in STANDARD_COLLECTION_TYPES:
            if standard_type.keyword_pattern.search(name):
                used.add(standard_type.key)
    return used
