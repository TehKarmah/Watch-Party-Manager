"""Tests for services/discord_ui_limits.py: the centralized safe-select
helpers every SelectOption-building view in this project should route
through, so a long channel/thread/collection name can never produce an
invalid Discord component payload (Setup Wizard Step 6 select-option
length failure fix).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import discord

from watch_party_manager.services.discord_ui_limits import (
    SELECT_MAX_OPTIONS,
    SELECT_OPTION_DESCRIPTION_MAX_LENGTH,
    SELECT_OPTION_LABEL_MAX_LENGTH,
    SELECT_OPTION_VALUE_MAX_LENGTH,
    assert_view_serializes_within_discord_limits,
    build_safe_select_option,
    cap_select_options,
    find_oversized_view_component_fields,
    truncate_for_discord,
)


class TruncateForDiscordTests(unittest.TestCase):
    def test_none_passes_through_unchanged(self) -> None:
        self.assertIsNone(truncate_for_discord(None))

    def test_short_text_is_unchanged(self) -> None:
        self.assertEqual(truncate_for_discord("short"), "short")

    def test_long_text_is_truncated_to_the_default_max_length(self) -> None:
        text = "x" * 150
        self.assertEqual(len(truncate_for_discord(text)), 100)

    def test_respects_a_custom_max_length(self) -> None:
        self.assertEqual(len(truncate_for_discord("x" * 50, max_length=10)), 10)

    def test_a_string_of_astral_plane_characters_is_truncated_by_utf16_length_not_python_length(self) -> None:
        # TASK D root cause: Discord measures string-length limits in
        # UTF-16 code units, the same way JavaScript's String.length
        # does -- not Python's code-point-based len(). Most emoji (e.g.
        # U+1F3AC below) sit outside the Basic Multilingual Plane and are
        # encoded as a UTF-16 surrogate pair: 2 units to Discord, but
        # only 1 to Python's len(). A naive text[:100] left a string that
        # was <=100 Python characters but up to 200 Discord length units
        # -- still rejected by Discord's API as over the limit even
        # though it "looked" safely truncated. Truncation must measure
        # UTF-16 units, not code points.
        text = "\U0001F3AC" * 150
        truncated = truncate_for_discord(text)
        utf16_length = len(truncated.encode("utf-16-le")) // 2
        self.assertLessEqual(utf16_length, 100)

    def test_astral_plane_truncation_never_splits_a_character_into_an_invalid_surrogate(self) -> None:
        text = "\U0001F3AC" * 150
        truncated = truncate_for_discord(text)
        # Every character round-trips through UTF-16 encode/decode
        # cleanly -- if truncation ever cut a surrogate pair in half,
        # this encode would raise (or silently produce mojibake).
        truncated.encode("utf-16-le").decode("utf-16-le")
        self.assertTrue(all(character == "\U0001F3AC" for character in truncated))

    def test_a_mix_of_ascii_and_astral_characters_is_truncated_correctly(self) -> None:
        text = "Thread in #" + ("\U0001F3AC" * 90)
        truncated = truncate_for_discord(text)
        utf16_length = len(truncated.encode("utf-16-le")) // 2
        self.assertLessEqual(utf16_length, 100)
        self.assertTrue(truncated.startswith("Thread in #"))

    def test_a_short_astral_character_string_is_unchanged(self) -> None:
        text = "\U0001F3AC" * 10
        self.assertEqual(truncate_for_discord(text), text)


class BuildSafeSelectOptionTests(unittest.TestCase):
    def test_short_fields_pass_through_unchanged(self) -> None:
        option = build_safe_select_option("Movies", "1", description="A collection")
        self.assertEqual(option.label, "Movies")
        self.assertEqual(option.value, "1")
        self.assertEqual(option.description, "A collection")

    def test_a_long_label_is_truncated_to_the_limit(self) -> None:
        option = build_safe_select_option("x" * 150, "1")
        self.assertLessEqual(len(option.label), SELECT_OPTION_LABEL_MAX_LENGTH)

    def test_a_long_description_is_truncated_to_the_limit(self) -> None:
        # The exact scenario reported live: a generated description (here,
        # "Thread in #<parent-channel-name>") exceeding Discord's
        # 100-character SelectOption description limit, previously
        # producing a 400 Bad Request (error 50035) instead of ever
        # being safely shortened.
        long_parent_name = "x" * 100
        description = f"Thread in #{long_parent_name}"
        self.assertGreater(len(description), SELECT_OPTION_DESCRIPTION_MAX_LENGTH)

        option = build_safe_select_option("archive-thread", "123", description=description)

        self.assertLessEqual(len(option.description), SELECT_OPTION_DESCRIPTION_MAX_LENGTH)

    def test_a_long_value_is_truncated_to_the_limit(self) -> None:
        option = build_safe_select_option("Movies", "x" * 150)
        self.assertLessEqual(len(option.value), SELECT_OPTION_VALUE_MAX_LENGTH)

    def test_none_description_stays_none_rather_than_becoming_a_placeholder(self) -> None:
        option = build_safe_select_option("Movies", "1")
        self.assertIsNone(option.description)

    def test_default_and_emoji_are_forwarded_unchanged(self) -> None:
        option = build_safe_select_option("Movies", "1", default=True, emoji="🎬")
        self.assertTrue(option.default)
        self.assertEqual(str(option.emoji), "🎬")

    def test_an_empty_label_falls_back_to_a_non_empty_placeholder(self) -> None:
        # Discord requires a SelectOption's label to be at least one
        # character -- an empty string would itself be an invalid payload.
        option = build_safe_select_option("", "1")
        self.assertGreaterEqual(len(option.label), 1)


class CapSelectOptionsTests(unittest.TestCase):
    def _make_options(self, count: int, *, default_index: "int | None" = None):
        return [
            build_safe_select_option(f"Option {i}", str(i), default=(i == default_index))
            for i in range(count)
        ]

    def test_a_list_within_the_limit_is_returned_unchanged(self) -> None:
        options = self._make_options(10)
        self.assertEqual(cap_select_options(options), options)

    def test_more_than_25_options_is_capped_at_25(self) -> None:
        options = self._make_options(40)
        capped = cap_select_options(options)
        self.assertEqual(len(capped), SELECT_MAX_OPTIONS)

    def test_the_selected_option_is_preserved_even_past_the_cap(self) -> None:
        # The exact scenario Setup Wizard/config screens depend on: a
        # guild with more usable destinations than the 25-option cap
        # allows must never silently drop the already-saved selection.
        options = self._make_options(40, default_index=39)
        capped = cap_select_options(options)
        self.assertEqual(len(capped), SELECT_MAX_OPTIONS)
        self.assertTrue(any(option.value == "39" and option.default for option in capped))

    def test_no_default_option_still_caps_cleanly(self) -> None:
        options = self._make_options(30)
        capped = cap_select_options(options)
        self.assertEqual(len(capped), SELECT_MAX_OPTIONS)


class FindOversizedViewComponentFieldsTests(unittest.TestCase):
    """The "final serialized-view validator" TASK D requires: walks the
    exact view.to_components() payload Discord will receive, catching
    anything a build_safe_select_option() call site couldn't have seen
    (e.g. a raw discord.SelectOption(...) construction, or any other
    unaudited option builder), rather than trusting that every option
    passed through safe construction in the first place.
    """

    def _view_with(self, options) -> discord.ui.View:
        view = discord.ui.View()
        view.add_item(discord.ui.Select(options=options))
        return view

    def test_a_safely_built_view_has_no_violations(self) -> None:
        view = self._view_with(
            [build_safe_select_option("Movies", "1", description="A collection"), build_safe_select_option("TV", "2")]
        )
        self.assertEqual(find_oversized_view_component_fields(view), [])

    def test_a_raw_oversized_description_is_caught(self) -> None:
        # Exactly the class of bug build_safe_select_option() can't catch
        # on its own: a raw discord.SelectOption(...) built without it.
        view = self._view_with(
            [
                discord.SelectOption(label="a", value="1"),
                discord.SelectOption(label="b", value="2", description="d" * 150),
            ]
        )
        violations = find_oversized_view_component_fields(view)
        self.assertEqual(len(violations), 1)
        self.assertIn("option 1", violations[0])
        self.assertIn("description", violations[0])

    def test_an_astral_character_description_within_python_len_but_over_discord_limit_is_caught(self) -> None:
        # The exact TASK D failure mode: build_safe_select_option's own
        # OLD code-point-based truncate_for_discord would have let this
        # through (<=100 Python characters) even though Discord's
        # UTF-16-based count is 178. The current implementation fixes
        # this at the source (truncate_for_discord itself), but this
        # validator must independently catch it too, since it has to
        # protect against option builders that don't call
        # build_safe_select_option() at all.
        description = "\U0001F3AC" * 89  # 89 Python chars, 178 UTF-16 units
        view = self._view_with([discord.SelectOption(label="a", value="1", description=description)])
        violations = find_oversized_view_component_fields(view)
        self.assertEqual(len(violations), 1)

    def test_more_than_25_options_in_one_select_is_caught(self) -> None:
        options = [discord.SelectOption(label=f"Option {i}", value=str(i)) for i in range(30)]
        view = self._view_with(options)
        violations = find_oversized_view_component_fields(view)
        self.assertTrue(any("options" in violation and "25" in violation for violation in violations))

    def test_a_view_with_no_selects_has_no_violations(self) -> None:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Click me"))
        self.assertEqual(find_oversized_view_component_fields(view), [])


class AssertViewSerializesWithinDiscordLimitsTests(unittest.TestCase):
    def test_a_safe_view_does_not_raise(self) -> None:
        view = discord.ui.View()
        view.add_item(discord.ui.Select(options=[build_safe_select_option("Movies", "1")]))
        assert_view_serializes_within_discord_limits(view)  # must not raise

    def test_an_unsafe_view_raises_with_the_violation_details(self) -> None:
        view = discord.ui.View()
        view.add_item(
            discord.ui.Select(
                options=[discord.SelectOption(label="a", value="1", description="d" * 150)]
            )
        )
        with self.assertRaises(ValueError) as ctx:
            assert_view_serializes_within_discord_limits(view)
        self.assertIn("description", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
