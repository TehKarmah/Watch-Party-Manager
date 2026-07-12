"""Persistence layer for Watch Party Manager."""

from watch_party_manager.persistence.suggestion_repository import (
    DEFAULT_SUGGESTIONS_PATH,
    JsonSuggestionRepository,
    LoadResult,
)
from watch_party_manager.persistence.vote_repository import (
    DEFAULT_VOTING_PATH,
    JsonVoteRepository,
    VoteLoadResult,
)

__all__ = [
    "DEFAULT_SUGGESTIONS_PATH",
    "JsonSuggestionRepository",
    "LoadResult",
    "DEFAULT_VOTING_PATH",
    "JsonVoteRepository",
    "VoteLoadResult",
]
