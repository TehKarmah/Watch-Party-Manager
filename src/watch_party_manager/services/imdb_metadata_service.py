"""IMDb URL parsing and OMDb-backed title metadata resolution."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_IMDB_TITLE_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?imdb\.com/title/(tt\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)
_OMDB_API_URL = "https://www.omdbapi.com/"


@dataclass(frozen=True)
class ImdbTitleResult:
    """Result of resolving a title from an IMDb title URL."""

    success: bool
    imdb_url: Optional[str] = None
    imdb_id: Optional[str] = None
    title: Optional[str] = None
    error_message: Optional[str] = None


class ImdbMetadataService:
    """Resolve IMDb title links through the OMDb JSON API.

    The API key defaults to ``OMDB_API_KEY`` from the environment. Tests and
    callers may inject ``fetch_json`` to keep behavior deterministic and avoid
    network access.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        fetch_json: Optional[Callable[[str], Any]] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        configured_key = api_key if api_key is not None else os.getenv("OMDB_API_KEY", "")
        self._api_key = configured_key.strip()
        self._fetch_json = fetch_json or self._fetch_json_from_web
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
        """Resolve an IMDb title URL into its canonical URL and display title."""
        canonical_url = self.normalize_imdb_url(value)
        if canonical_url is None:
            return ImdbTitleResult(
                success=False,
                error_message="That does not look like a valid IMDb title link.",
            )

        imdb_id = canonical_url.rstrip("/").rsplit("/", 1)[-1]
        if not self._api_key:
            return ImdbTitleResult(
                success=False,
                imdb_url=canonical_url,
                imdb_id=imdb_id,
                error_message=(
                    "IMDb lookup is not configured. Add OMDB_API_KEY to the .env "
                    "file and restart WASH."
                ),
            )

        request_url = self._build_request_url(imdb_id)
        try:
            payload = await asyncio.to_thread(self._fetch_json, request_url)
        except Exception:
            return ImdbTitleResult(
                success=False,
                imdb_url=canonical_url,
                imdb_id=imdb_id,
                error_message=(
                    "I could not retrieve that title from OMDb. Try again, or "
                    "provide the watch item title and IMDb link separately."
                ),
            )

        parsed = self._coerce_payload(payload)
        if parsed is None:
            return ImdbTitleResult(
                success=False,
                imdb_url=canonical_url,
                imdb_id=imdb_id,
                error_message="OMDb returned an unreadable response for that IMDb link.",
            )

        if str(parsed.get("Response", "True")).lower() == "false":
            detail = str(parsed.get("Error", "Title not found.")).strip() or "Title not found."
            return ImdbTitleResult(
                success=False,
                imdb_url=canonical_url,
                imdb_id=imdb_id,
                error_message=f"OMDb could not resolve that IMDb title: {detail}",
            )

        title = self._format_display_title(parsed.get("Title"), parsed.get("Year"))
        if title is None:
            return ImdbTitleResult(
                success=False,
                imdb_url=canonical_url,
                imdb_id=imdb_id,
                error_message="OMDb found the title but did not return a usable name.",
            )

        return ImdbTitleResult(
            success=True,
            imdb_url=canonical_url,
            imdb_id=imdb_id,
            title=title,
        )

    def _build_request_url(self, imdb_id: str) -> str:
        query = urlencode({"apikey": self._api_key, "i": imdb_id, "plot": "short", "r": "json"})
        return f"{_OMDB_API_URL}?{query}"

    @staticmethod
    def _coerce_payload(payload: Any) -> Optional[dict[str, Any]]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")
        if not isinstance(payload, str):
            return None
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return decoded if isinstance(decoded, dict) else None

    @staticmethod
    def _format_display_title(title: Any, year: Any) -> Optional[str]:
        if not isinstance(title, str):
            return None
        clean_title = re.sub(r"\s+", " ", title).strip()
        if not clean_title or clean_title.lower() == "n/a":
            return None

        clean_year = str(year).strip() if year is not None else ""
        if clean_year and clean_year.lower() != "n/a":
            return f"{clean_title} ({clean_year})"
        return clean_title

    def _fetch_json_from_web(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "User-Agent": "WASH/1.0 (Watch Party Administration & Scheduling Helper)",
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("OMDb returned a non-object JSON response")
        return payload
