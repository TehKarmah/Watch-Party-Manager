"""Normalize user input before creating a watch-item suggestion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService


@dataclass(frozen=True)
class ResolvedSuggestionInput:
    """Normalized title and IMDb metadata ready for SuggestionService."""

    success: bool
    title: Optional[str] = None
    imdb_url: Optional[str] = None
    error_message: Optional[str] = None


class SuggestionInputService:
    """Accept title-first or IMDb-link-first suggestion input."""

    def __init__(self, imdb_metadata_service: Optional[ImdbMetadataService] = None) -> None:
        self._imdb_metadata_service = imdb_metadata_service or ImdbMetadataService()

    async def resolve(
        self,
        title: str,
        imdb_url: Optional[str] = None,
    ) -> ResolvedSuggestionInput:
        """Normalize a suggestion before it is persisted.

        A normal title is preserved. When the title field itself contains an
        IMDb title URL, the page title is resolved and the link is moved into
        the IMDb metadata field.
        """
        cleaned_title = title.strip() if title else ""
        cleaned_imdb_url = imdb_url.strip() if imdb_url and imdb_url.strip() else None

        if not cleaned_title:
            return ResolvedSuggestionInput(
                success=False,
                error_message="I need a title or IMDb link before I can add it.",
            )

        if not self._imdb_metadata_service.is_imdb_title_url(cleaned_title):
            if cleaned_imdb_url is not None:
                canonical_url = self._imdb_metadata_service.normalize_imdb_url(cleaned_imdb_url)
                if canonical_url is None:
                    return ResolvedSuggestionInput(
                        success=False,
                        error_message="That does not look like a valid IMDb title link.",
                    )
                cleaned_imdb_url = canonical_url
            return ResolvedSuggestionInput(
                success=True,
                title=cleaned_title,
                imdb_url=cleaned_imdb_url,
            )

        if cleaned_imdb_url is not None and cleaned_imdb_url != cleaned_title:
            return ResolvedSuggestionInput(
                success=False,
                error_message=(
                    "Use either the title field for the IMDb link or provide the "
                    "movie title and IMDb link separately, not two different links."
                ),
            )

        resolved = await self._imdb_metadata_service.resolve_title(cleaned_title)
        if not resolved.success:
            return ResolvedSuggestionInput(
                success=False,
                error_message=resolved.error_message,
            )

        return ResolvedSuggestionInput(
            success=True,
            title=resolved.title,
            imdb_url=resolved.imdb_url,
        )
