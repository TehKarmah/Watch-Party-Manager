"""IMDb URL parsing and lightweight title metadata resolution."""

from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable, Optional
from urllib.request import Request, urlopen

_IMDB_TITLE_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?imdb\.com/title/(tt\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)
_YEAR_SUFFIX_PATTERN = re.compile(r"\s+\((?:18|19|20)\d{2}\)$")
_IMDB_SUFFIX_PATTERN = re.compile(r"\s*[-|]\s*IMDb\s*$", re.IGNORECASE)


class _ImdbPageMetadataParser(HTMLParser):
    """Collect title candidates without depending on IMDb's attribute order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open_graph_title: Optional[str] = None
        self.html_title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.json_ld_blocks: list[str] = []
        self._in_title = False
        self._in_primary_heading = False
        self._in_json_ld = False
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = {name.lower(): value or "" for name, value in attrs}
        lowered_tag = tag.lower()

        if lowered_tag == "meta":
            property_name = attributes.get("property", "").lower()
            metadata_name = attributes.get("name", "").lower()
            if property_name == "og:title" or metadata_name == "og:title":
                content = attributes.get("content", "").strip()
                if content and self.open_graph_title is None:
                    self.open_graph_title = content
            return

        if lowered_tag == "title":
            self._in_title = True
            return

        if lowered_tag == "h1":
            class_names = attributes.get("class", "").lower()
            # IMDb currently labels its main title with hero__primary-text.
            # An unclassified h1 remains a useful last-resort fallback.
            self._in_primary_heading = (
                "hero__primary-text" in class_names or not class_names
            )
            return

        if lowered_tag == "script":
            script_type = attributes.get("type", "").split(";", 1)[0].strip().lower()
            if script_type == "application/ld+json":
                self._in_json_ld = True
                self._json_ld_parts = []

    def handle_endtag(self, tag: str) -> None:
        lowered_tag = tag.lower()
        if lowered_tag == "title":
            self._in_title = False
        elif lowered_tag == "h1":
            self._in_primary_heading = False
        elif lowered_tag == "script" and self._in_json_ld:
            block = "".join(self._json_ld_parts).strip()
            if block:
                self.json_ld_blocks.append(block)
            self._in_json_ld = False
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.html_title_parts.append(data)
        if self._in_primary_heading:
            self.heading_parts.append(data)
        if self._in_json_ld:
            self._json_ld_parts.append(data)


@dataclass(frozen=True)
class ImdbTitleResult:
    """Result of resolving a title from an IMDb title URL."""

    success: bool
    imdb_url: Optional[str] = None
    imdb_id: Optional[str] = None
    title: Optional[str] = None
    error_message: Optional[str] = None


class ImdbMetadataService:
    """Resolve a display title from an IMDb title URL.

    The default resolver uses only the Python standard library. Tests and
    callers may inject ``fetch_html`` to keep behavior deterministic and avoid
    network access.
    """

    def __init__(
        self,
        fetch_html: Optional[Callable[[str], str]] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._fetch_html = fetch_html or self._fetch_html_from_web
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def is_imdb_title_url(value: str) -> bool:
        """Return whether ``value`` identifies an IMDb title page."""
        if not value:
            return False
        return _IMDB_TITLE_PATTERN.fullmatch(value.strip()) is not None

    @staticmethod
    def normalize_imdb_url(value: str) -> Optional[str]:
        """Return a canonical IMDb title URL, or ``None`` when invalid."""
        if not value:
            return None
        match = _IMDB_TITLE_PATTERN.fullmatch(value.strip())
        if match is None:
            return None
        return f"https://www.imdb.com/title/{match.group(1).lower()}/"

    async def resolve_title(self, value: str) -> ImdbTitleResult:
        """Resolve an IMDb title URL into its canonical URL and watch-item title."""
        canonical_url = self.normalize_imdb_url(value)
        if canonical_url is None:
            return ImdbTitleResult(
                success=False,
                error_message="That does not look like a valid IMDb title link.",
            )

        imdb_id = canonical_url.rstrip("/").rsplit("/", 1)[-1]
        try:
            page_html = await asyncio.to_thread(self._fetch_html, canonical_url)
        except Exception:
            return ImdbTitleResult(
                success=False,
                imdb_url=canonical_url,
                imdb_id=imdb_id,
                error_message=(
                    "I could not retrieve that IMDb title. Try again, or provide "
                    "the watch item title and IMDb link separately."
                ),
            )

        title = self._extract_title(page_html)
        if title is None:
            title = await self._resolve_from_suggestion_data(imdb_id)
        if title is None:
            return ImdbTitleResult(
                success=False,
                imdb_url=canonical_url,
                imdb_id=imdb_id,
                error_message=(
                    "I found the IMDb page but could not determine its title. "
                    "Provide the watch item title and IMDb link separately."
                ),
            )

        return ImdbTitleResult(
            success=True,
            imdb_url=canonical_url,
            imdb_id=imdb_id,
            title=title,
        )

    async def _resolve_from_suggestion_data(self, imdb_id: str) -> Optional[str]:
        """Use IMDb's lightweight title-suggestion data as a metadata fallback."""
        suggestion_url = f"https://v2.sg.media-imdb.com/suggestion/x/{imdb_id}.json"
        try:
            raw_data = await asyncio.to_thread(self._fetch_html, suggestion_url)
        except Exception:
            return None
        return self._extract_title_from_suggestion_data(raw_data, imdb_id)

    @classmethod
    def _extract_title(cls, page_html: str) -> Optional[str]:
        """Extract and normalize a title from current or legacy IMDb HTML."""
        if not page_html:
            return None

        parser = _ImdbPageMetadataParser()
        try:
            parser.feed(page_html)
            parser.close()
        except Exception:
            # Partially malformed HTML should not prevent the remaining
            # candidates collected before the parse error from being used.
            pass

        candidates: list[Optional[str]] = [parser.open_graph_title]
        candidates.extend(cls._extract_json_ld_titles(parser.json_ld_blocks))
        candidates.append("".join(parser.heading_parts))
        candidates.append("".join(parser.html_title_parts))

        for candidate in candidates:
            normalized = cls._normalize_title(candidate)
            if normalized:
                return normalized
        return None

    @classmethod
    def _extract_json_ld_titles(cls, blocks: list[str]) -> list[str]:
        titles: list[str] = []
        for block in blocks:
            try:
                payload = json.loads(block)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            cls._collect_json_ld_titles(payload, titles)
        return titles

    @classmethod
    def _collect_json_ld_titles(cls, value: Any, titles: list[str]) -> None:
        if isinstance(value, list):
            for entry in value:
                cls._collect_json_ld_titles(entry, titles)
            return
        if not isinstance(value, dict):
            return

        entity_type = value.get("@type")
        supported_types = {
            "movie",
            "tvseries",
            "tvminiseries",
            "tvepisode",
            "creativework",
            "videoobject",
        }
        normalized_types = {
            str(item).lower()
            for item in (entity_type if isinstance(entity_type, list) else [entity_type])
            if item
        }
        name = value.get("name")
        if isinstance(name, str) and (not normalized_types or normalized_types & supported_types):
            titles.append(name)

        for key in ("@graph", "mainEntity", "itemListElement"):
            nested = value.get(key)
            if nested is not None:
                cls._collect_json_ld_titles(nested, titles)

    @classmethod
    def _extract_title_from_suggestion_data(
        cls,
        raw_data: str,
        imdb_id: str,
    ) -> Optional[str]:
        if not raw_data:
            return None
        try:
            payload = json.loads(raw_data)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

        entries = payload.get("d", []) if isinstance(payload, dict) else []
        if not isinstance(entries, list):
            return None

        preferred: list[dict[str, Any]] = []
        fallback: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("id", "")).lower() == imdb_id.lower():
                preferred.append(entry)
            else:
                fallback.append(entry)

        for entry in preferred + fallback:
            title = cls._normalize_title(entry.get("l"))
            if title:
                return title
        return None

    @staticmethod
    def _normalize_title(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        title = html.unescape(value)
        title = re.sub(r"\s+", " ", title).strip()
        title = _IMDB_SUFFIX_PATTERN.sub("", title).strip()
        title = _YEAR_SUFFIX_PATTERN.sub("", title).strip()
        return title or None

    def _fetch_html_from_web(self, url: str) -> str:
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/json;q=0.9,"
                    "*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:
            return response.read().decode("utf-8", errors="replace")
