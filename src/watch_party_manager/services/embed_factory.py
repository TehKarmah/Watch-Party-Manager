"""Reusable Discord embed builders for WASH commands."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

WASH_INFO_COLOR = 0xF5C518
WASH_SUCCESS_COLOR = 0x57F287
WASH_WARNING_COLOR = 0xFEE75C
WASH_ERROR_COLOR = 0xED4245
WASH_EMBED_FOOTER = "Watch Party Manager • TehKarmah"


class EmbedFactory:
    """Create consistently styled Discord embeds for WASH.

    Discord is imported lazily so this module remains importable in tooling and
    service-level tests that do not install discord.py.
    """

    @classmethod
    def info(
        cls,
        title: str,
        description: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Create a standard informational embed."""
        return cls._build(
            title=title,
            description=description,
            color=WASH_INFO_COLOR,
            **kwargs,
        )

    @classmethod
    def success(
        cls,
        title: str,
        description: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Create an embed for a successfully completed action."""
        return cls._build(
            title=title,
            description=description,
            color=WASH_SUCCESS_COLOR,
            **kwargs,
        )

    @classmethod
    def warning(
        cls,
        title: str,
        description: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Create an embed for a warning or recoverable problem."""
        return cls._build(
            title=title,
            description=description,
            color=WASH_WARNING_COLOR,
            **kwargs,
        )

    @classmethod
    def error(
        cls,
        title: str,
        description: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Create an embed for a failed action or blocking problem."""
        return cls._build(
            title=title,
            description=description,
            color=WASH_ERROR_COLOR,
            **kwargs,
        )

    @staticmethod
    def _build(
        *,
        title: str,
        description: Optional[str],
        color: int,
        url: Optional[str] = None,
        fields: Iterable[Mapping[str, Any]] = (),
        footer: str = WASH_EMBED_FOOTER,
        timestamp: Optional[datetime] = None,
        include_timestamp: bool = True,
    ) -> Any:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("title is required")

        clean_description = description.strip() if description else None
        if not clean_description:
            clean_description = None
        clean_url = url.strip() if url else None
        clean_footer = footer.strip()
        if not clean_footer:
            raise ValueError("footer is required")

        if timestamp is not None:
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("timestamp must be timezone-aware")
            resolved_timestamp = timestamp
        elif include_timestamp:
            resolved_timestamp = datetime.now(timezone.utc)
        else:
            resolved_timestamp = None

        import discord

        embed = discord.Embed(
            title=clean_title,
            description=clean_description,
            url=clean_url,
            color=color,
            timestamp=resolved_timestamp,
        )

        for field in fields:
            name = str(field.get("name", "")).strip()
            value = str(field.get("value", "")).strip()
            if not name or not value:
                raise ValueError("embed fields require non-blank name and value")
            embed.add_field(
                name=name,
                value=value,
                inline=bool(field.get("inline", False)),
            )

        embed.set_footer(text=clean_footer)
        return embed
