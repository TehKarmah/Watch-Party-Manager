"""UX Polish audit: every top-level slash command must have an explicit,
Discord-visible description.

discord.py falls back to a bare "…" in Discord's command picker when a
command has neither a `description=` kwarg nor a docstring on its
callback -- none of bot.py's top-level `@self.tree.command(...)`
callbacks have docstrings (they're thin wrappers delegating to a
`handle_*`/`perform_*` function), so `description=` is the only way
any of them get a real description. A prior UX audit found every
single one of the 17 top-level commands was missing this.

Implemented as a static AST check rather than constructing a live
WatchPartyBot, since setup_hook() registers commands via nested
closures and isn't independently callable in a unit test (see
test_interactive_voting.py's own note on this).
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

BOT_PY_PATH = Path(__file__).resolve().parents[1] / "src" / "watch_party_manager" / "bot.py"


def _find_top_level_command_calls() -> list[ast.Call]:
    tree = ast.parse(BOT_PY_PATH.read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "command"):
            continue
        if not (isinstance(func.value, ast.Attribute) and func.value.attr == "tree"):
            continue
        calls.append(node)
    return calls


class TopLevelCommandDescriptionsTests(unittest.TestCase):
    def test_every_top_level_command_has_a_description(self) -> None:
        calls = _find_top_level_command_calls()
        self.assertTrue(calls, "Expected to find @self.tree.command(...) registrations in bot.py")

        missing = []
        for call in calls:
            name = None
            description = None
            for keyword in call.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    name = keyword.value.value
                if keyword.arg == "description" and isinstance(keyword.value, ast.Constant):
                    description = keyword.value.value
            if name is not None and not description:
                missing.append(name)

        self.assertEqual([], missing, f"Top-level commands missing a description: {missing}")

    def test_at_least_the_known_top_level_commands_are_covered(self) -> None:
        # A sanity check on the AST walk itself, so a refactor that
        # changes how commands are registered can't silently make the
        # test above pass by finding zero commands.
        calls = _find_top_level_command_calls()
        names = {
            keyword.value.value
            for call in calls
            for keyword in call.keywords
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant)
        }
        expected = {
            "about",
            "join_watch_party",
            "help",
            "stats",
            "add",
            "list",
            "repair_suggestions",
            "backup",
            "restore",
            "factory_reset",
            "import",
            "remove",
            "edit_suggestion",
            "reject",
            "unreject",
            "setup",
            "config",
        }
        self.assertTrue(expected.issubset(names))


if __name__ == "__main__":
    unittest.main()
