"""Persistence layer for Watch Party Manager."""

from watch_party_manager.persistence.guild_configuration_repository import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_GUILD_CONFIGURATIONS_PATH,
    FutureSchemaVersionError,
    GuildConfigurationRepository,
)

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
from watch_party_manager.persistence.suggestion_database_repository import (
    DEFAULT_SUGGESTION_DATABASES_PATH,
    JsonSuggestionDatabaseRepository,
    SuggestionDatabaseLoadResult,
)
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    CURRENT_SCHEMA_VERSION as SUGGESTION_DATABASE_CONFIGURATION_SCHEMA_VERSION,
    DEFAULT_SUGGESTION_DATABASE_CONFIGURATIONS_PATH,
    SuggestionDatabaseConfigurationRepository,
)

__all__ = [
    "DEFAULT_SUGGESTIONS_PATH",
    "JsonSuggestionRepository",
    "LoadResult",
    "DEFAULT_VOTING_PATH",
    "JsonVoteRepository",
    "VoteLoadResult",
    "DEFAULT_SUGGESTION_DATABASES_PATH",
    "JsonSuggestionDatabaseRepository",
    "SuggestionDatabaseLoadResult",
    "GuildConfigurationRepository",
    "FutureSchemaVersionError",
    "DEFAULT_GUILD_CONFIGURATIONS_PATH",
    "CURRENT_SCHEMA_VERSION",
    "SuggestionDatabaseConfigurationRepository",
    "DEFAULT_SUGGESTION_DATABASE_CONFIGURATIONS_PATH",
    "SUGGESTION_DATABASE_CONFIGURATION_SCHEMA_VERSION",
]
