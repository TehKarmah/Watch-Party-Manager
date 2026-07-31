"""Centralized Discord message-component limits and safe builders.

discord.py performs no client-side length validation on SelectOption's
label/description/value (see discord.SelectOption.__init__) -- an
oversized field is only ever caught when Discord's API rejects the
request with a 400 Bad Request (error 50035: Invalid Form Body), after
whatever action triggered the render (e.g. creating a new thread) has
already irreversibly happened. Every option list this project sends to
Discord should be built through build_safe_select_option()/
cap_select_options() so a long channel name, thread name, parent-channel
name, or generated description can never produce an invalid payload.

Length limits are UTF-16 code units, not Python characters. Discord's API
measures string-length limits (channel names, select option label/
description/value, etc.) the same way JavaScript's String.length does:
one unit per UTF-16 code unit, so any character outside the Basic
Multilingual Plane (BMP) -- most emoji, e.g. U+1F3AC, plus a handful of
rarer CJK/math characters -- counts as 2 units, encoded as a surrogate
pair. Python's str is indexed by Unicode code point, so len("🎬") == 1
even though Discord counts it as 2. A naive text[:100] slice can leave a
string that is <=100 Python characters but well over 100 by Discord's own
count whenever it contains astral-plane characters (see
_utf16_length()/truncate_for_discord() below) -- this is exactly what let
a channel/thread name containing emoji still produce a 400 Bad Request
after build_safe_select_option()'s original code-point-counting
truncation appeared to already bound it safely.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

import discord

# Discord's own hard limits for a select menu's options -- see
# https://discord.com/developers/docs/interactions/message-components#select-menu-object-select-option-structure
SELECT_OPTION_LABEL_MAX_LENGTH = 100
SELECT_OPTION_DESCRIPTION_MAX_LENGTH = 100
SELECT_OPTION_VALUE_MAX_LENGTH = 100
SELECT_MAX_OPTIONS = 25

# A label can never be sent empty (Discord requires at least one
# character) -- used only as a last-resort fallback if truncation ever
# somehow left an empty string (e.g. a caller passed one in).
_EMPTY_LABEL_FALLBACK = "​"

# Code points at or below this value fit in a single UTF-16 code unit
# (the Basic Multilingual Plane). Anything above it is encoded as a
# surrogate pair -- 2 units -- when Discord measures string length.
_MAX_BASIC_MULTILINGUAL_PLANE_CODEPOINT = 0xFFFF


def _utf16_length(text: str) -> int:
    """Count `text`'s length the way Discord's API does: UTF-16 code
    units, not Python code points (see module docstring).
    """
    return sum(2 if ord(character) > _MAX_BASIC_MULTILINGUAL_PLANE_CODEPOINT else 1 for character in text)


def truncate_for_discord(text: Optional[str], *, max_length: int = SELECT_OPTION_LABEL_MAX_LENGTH) -> Optional[str]:
    """Safely shorten `text` to at most `max_length` Discord length units.

    None passes through unchanged -- a genuinely absent field (e.g. "no
    description") must never be coerced into the literal string "None"
    or an empty string that changes its meaning.

    Measures and truncates by UTF-16 code units (see _utf16_length), not
    Python's code-point-based len()/slicing -- a plain text[:max_length]
    can leave a string that is <=max_length Python characters but over
    the limit by Discord's own count whenever it contains astral-plane
    characters such as most emoji. Always cuts on a whole-character
    boundary, so a 2-unit character is never split into an invalid lone
    surrogate.
    """
    if text is None:
        return None
    if _utf16_length(text) <= max_length:
        return text
    kept_characters: List[str] = []
    units_used = 0
    for character in text:
        character_units = 2 if ord(character) > _MAX_BASIC_MULTILINGUAL_PLANE_CODEPOINT else 1
        if units_used + character_units > max_length:
            break
        kept_characters.append(character)
        units_used += character_units
    return "".join(kept_characters)


def build_safe_select_option(
    label: str,
    value: str,
    *,
    description: Optional[str] = None,
    default: bool = False,
    emoji: Optional[object] = None,
) -> discord.SelectOption:
    """Build a discord.SelectOption guaranteed to satisfy Discord's
    label/description/value length limits (100 characters each),
    regardless of how long the caller's underlying channel, thread, or
    collection name is.

    Every SelectOption this project sends to Discord should be built
    through this function rather than discord.SelectOption(...) directly
    -- that is what makes the 100-character limit a property of the
    codebase rather than something every call site has to remember.
    """
    safe_label = truncate_for_discord(label, max_length=SELECT_OPTION_LABEL_MAX_LENGTH) or _EMPTY_LABEL_FALLBACK
    return discord.SelectOption(
        label=safe_label,
        value=truncate_for_discord(value, max_length=SELECT_OPTION_VALUE_MAX_LENGTH),
        description=truncate_for_discord(description, max_length=SELECT_OPTION_DESCRIPTION_MAX_LENGTH),
        default=default,
        emoji=emoji,
    )


def cap_select_options(
    options: Sequence[discord.SelectOption], *, max_options: int = SELECT_MAX_OPTIONS
) -> List[discord.SelectOption]:
    """Cap a select's option list at Discord's hard 25-option limit.

    Whichever option (if any) is marked `default` is always kept, moved
    to the front if it would otherwise fall outside the first
    `max_options` -- so an already-selected destination is never
    silently dropped just because the guild now has more usable options
    than the cap allows.
    """
    ordered = list(options)
    if len(ordered) <= max_options:
        return ordered
    selected = next((option for option in ordered if option.default), None)
    if selected is not None and selected in ordered[max_options:]:
        ordered.remove(selected)
        ordered.insert(0, selected)
    return ordered[:max_options]


def find_oversized_view_component_fields(view: discord.ui.View) -> List[str]:
    """Walk `view.to_components()` -- the exact JSON payload Discord will
    receive for this view, not the Python SelectOption objects sitting in
    memory -- and return one human-readable description per select-menu
    field that violates Discord's limits.

    An empty list means the view is safe to send. This is the final
    safety net: build_safe_select_option()/cap_select_options() make
    correct construction the easy path, but they can only constrain a
    field at the moment it's built. Calling this on the view actually
    about to be sent (after every prefix, suffix, retained-selection
    annotation, etc. has been assembled) catches anything built or
    mutated some other way -- a raw discord.SelectOption(...) call, a
    view/select subclass that reconstructs its own options, or a field
    changed after construction -- that build_safe_select_option()
    couldn't have seen. Length is measured the way Discord measures it
    (UTF-16 code units, see _utf16_length) -- a field within Python's
    len() can still be reported here if it contains astral-plane
    characters.
    """
    violations: List[str] = []
    for row_index, row in enumerate(view.to_components()):
        for component_index, component in enumerate(row.get("components", []) or []):
            options = component.get("options")
            if options is None:
                continue
            if len(options) > SELECT_MAX_OPTIONS:
                violations.append(
                    f"row {row_index} component {component_index} has {len(options)} options "
                    f"(max {SELECT_MAX_OPTIONS})"
                )
            for option_index, option in enumerate(options):
                for field_name, max_length in (
                    ("label", SELECT_OPTION_LABEL_MAX_LENGTH),
                    ("description", SELECT_OPTION_DESCRIPTION_MAX_LENGTH),
                    ("value", SELECT_OPTION_VALUE_MAX_LENGTH),
                ):
                    field_value = option.get(field_name)
                    if not field_value:
                        continue
                    field_length = _utf16_length(field_value)
                    if field_length > max_length:
                        violations.append(
                            f"row {row_index} component {component_index} option {option_index} "
                            f"{field_name} is {field_length} Discord (UTF-16) length units, "
                            f"{len(field_value)} Python len() (max {max_length}), default={option.get('default')}: "
                            f"{field_value!r}"
                        )
    return violations


def assert_view_serializes_within_discord_limits(view: discord.ui.View) -> None:
    """Raise ValueError with every violation found if `view` would be
    rejected by Discord as an oversized payload -- see
    find_oversized_view_component_fields(). A no-op when the view is
    safe.
    """
    violations = find_oversized_view_component_fields(view)
    if violations:
        raise ValueError("View would be rejected by Discord: " + "; ".join(violations))
