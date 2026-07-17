import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.services.embed_factory import (
    EmbedFactory,
    WASH_EMBED_FOOTER,
    WASH_ERROR_COLOR,
    WASH_INFO_COLOR,
    WASH_SUCCESS_COLOR,
    WASH_WARNING_COLOR,
)


class FakeEmbed:
    def __init__(
        self,
        *,
        title=None,
        description=None,
        url=None,
        color=None,
        timestamp=None,
    ) -> None:
        self.title = title
        self.description = description
        self.url = url
        self.color = color
        self.timestamp = timestamp
        self.fields = []
        self.footer = None

    def add_field(self, *, name, value, inline=False) -> None:
        self.fields.append({"name": name, "value": value, "inline": inline})

    def set_footer(self, *, text) -> None:
        self.footer = text


class EmbedFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.discord_patch = patch.dict(
            sys.modules,
            {"discord": SimpleNamespace(Embed=FakeEmbed)},
        )
        self.discord_patch.start()
        self.addCleanup(self.discord_patch.stop)

    def test_info_embed_uses_wash_accent_color(self) -> None:
        embed = EmbedFactory.info("Information", "Details")

        self.assertEqual(embed.title, "Information")
        self.assertEqual(embed.description, "Details")
        self.assertEqual(embed.color, WASH_INFO_COLOR)

    def test_success_embed_uses_success_color(self) -> None:
        embed = EmbedFactory.success("Saved")
        self.assertEqual(embed.color, WASH_SUCCESS_COLOR)

    def test_warning_embed_uses_warning_color(self) -> None:
        embed = EmbedFactory.warning("Check this")
        self.assertEqual(embed.color, WASH_WARNING_COLOR)

    def test_error_embed_uses_error_color(self) -> None:
        embed = EmbedFactory.error("Unable to continue")
        self.assertEqual(embed.color, WASH_ERROR_COLOR)

    def test_factory_applies_standard_footer_and_utc_timestamp(self) -> None:
        embed = EmbedFactory.info("Information")

        self.assertEqual(embed.footer, WASH_EMBED_FOOTER)
        self.assertIsNotNone(embed.timestamp)
        self.assertEqual(embed.timestamp.utcoffset(), timezone.utc.utcoffset(None))

    def test_factory_supports_url_and_fields(self) -> None:
        embed = EmbedFactory.info(
            "Information",
            url="https://example.com",
            fields=(
                {"name": "Section", "value": "Content"},
                {"name": "Compact", "value": "Value", "inline": True},
            ),
        )

        self.assertEqual(embed.url, "https://example.com")
        self.assertEqual(len(embed.fields), 2)
        self.assertFalse(embed.fields[0]["inline"])
        self.assertTrue(embed.fields[1]["inline"])

    def test_empty_description_is_omitted(self) -> None:
        embed = EmbedFactory.info("Information", "   ")
        self.assertIsNone(embed.description)

    def test_factory_rejects_blank_title_and_naive_timestamp(self) -> None:
        with self.assertRaisesRegex(ValueError, "title is required"):
            EmbedFactory.info("   ")

        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            EmbedFactory.info("Information", timestamp=datetime(2026, 7, 17))


if __name__ == "__main__":
    unittest.main()
