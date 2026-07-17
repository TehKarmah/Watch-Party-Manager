"""IMDb URL parsing and lightweight title metadata resolution."""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.request import Request, urlopen

_IMDB_TITLE_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?imdb\.com/title/(tt\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)
_OG_TITLE_PATTERN = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_TITLE_REVERSED_PATTERN = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
    re.IGNORECASE,
)
_HTML_TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_YEAR_SUFFIX_PATTERN = re.compile(r"\s+\((?:19|20)\d{2}\)$")


@dataclass(frozen=True)
class ImdbTitleResult:
    """Result of resolving a title from an IMDb title URL."""

    success: bool
    imdb_url: Optional[str] = None
    imdb_id: Optional[str] = None
    title: Optional[str] = None
    error_message: Optional[str] = None


class ImdbMetadataService:
    """Resolve a display title from an IMDb title page.

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
        """Resolve an IMDb title URL into its canonical URL and movie title."""
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
                    "the movie title and IMDb link separately."
                ),
            )

        title = self._extract_title(page_html)
        if title is None:
            return ImdbTitleResult(
                success=False,
                imdb_url=canonical_url,
                imdb_id=imdb_id,
                error_message=(
                    "I found the IMDb page but could not determine its title. "
                    "Provide the movie title and IMDb link separately."
                ),
            )

        return ImdbTitleResult(
            success=True,
            imdb_url=canonical_url,
            imdb_id=imdb_id,
            title=title,
        )

    @staticmethod
    def _extract_title(page_html: str) -> Optional[str]:
        """Extract and normalize the page title from IMDb HTML."""
        if not page_html:
            return None

        match = _OG_TITLE_PATTERN.search(page_html)
        if match is None:
            match = _OG_TITLE_REVERSED_PATTERN.search(page_html)
        if match is None:
            match = _HTML_TITLE_PATTERN.search(page_html)
        if match is None:
            return None

        title = html.unescape(match.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        title = re.sub(r"\s*[-|]\s*IMDb\s*$", "", title, flags=re.IGNORECASE).strip()
        title = _YEAR_SUFFIX_PATTERN.sub("", title).strip()
        return title or None

    def _fetch_html_from_web(self, url: str) -> str:
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; WASH/1.0; "
                    "+https://github.com/TehKarmah/Watch-Party-Manager)"
                )
            },
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:
            return response.read().decode("utf-8", errors="replace")
