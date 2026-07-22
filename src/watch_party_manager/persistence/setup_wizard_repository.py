"""JSON-backed persistence for FR-028's resumable /setup wizard state.

Mirrors GuildConfigurationRepository's shape (a single JSON document,
keyed by guild_id, written atomically via a temp-file-then-replace swap)
since this is the same kind of "one record per guild" persistence, just
for the wizard's own transient draft rather than the final configuration.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional, Union

from datetime import datetime

from watch_party_manager.domain.guild_configuration import GuildVoteVisibility, JoinMode
from watch_party_manager.domain.setup_wizard import (
    SetupWizardDraft,
    SetupWizardState,
    SetupWizardStep,
    SetupWizardStatus,
)
from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode

logger = logging.getLogger(__name__)
DEFAULT_SETUP_WIZARD_STATE_PATH = Path("data/setup_wizard_state.json")


class SetupWizardRepository:
    """Loads and saves in-progress /setup wizard state, one guild at a time."""

    def __init__(self, file_path: Union[Path, str] = DEFAULT_SETUP_WIZARD_STATE_PATH) -> None:
        self._file_path = Path(file_path)

    def get(self, guild_id: int) -> Optional[SetupWizardState]:
        """Return the in-progress wizard state for a guild, if any."""
        return self._load_all().get(guild_id)

    def save(self, state: SetupWizardState) -> None:
        """Create or update one guild's wizard state atomically."""
        states = self._load_all()
        states[state.guild_id] = state
        self._save_all(states)

    def delete(self, guild_id: int) -> bool:
        """Remove a guild's wizard state, e.g. after it completes or is cancelled.

        Returns:
            True if a record existed and was removed, False otherwise.
        """
        states = self._load_all()
        if guild_id not in states:
            return False
        del states[guild_id]
        self._save_all(states)
        return True

    def _load_all(self) -> dict[int, SetupWizardState]:
        if not self._file_path.exists():
            return {}
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            entries = data["guilds"]
            if not isinstance(entries, dict):
                raise TypeError("guilds must be an object")
            result: dict[int, SetupWizardState] = {}
            for guild_id_key, raw_entry in entries.items():
                state = self._deserialize(raw_entry)
                if str(state.guild_id) != str(guild_id_key):
                    raise ValueError("guild key does not match guild_id")
                result[state.guild_id] = state
            return result
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error("Could not load setup wizard state from %s: %s", self._file_path, exc)
            return {}

    def _save_all(self, states: dict[int, SetupWizardState]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"guilds": {str(key): self._serialize(value) for key, value in states.items()}}
        temporary_path = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
        temporary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary_path.replace(self._file_path)

    @staticmethod
    def _serialize(state: SetupWizardState) -> dict[str, Any]:
        draft = state.draft
        return {
            "guild_id": state.guild_id,
            "status": state.status.value,
            "current_step": state.current_step.value,
            "completed_steps": [step.value for step in state.completed_steps],
            "started_at": state.started_at.isoformat(),
            "updated_at": state.updated_at.isoformat(),
            "draft": {
                "wash_crew_role_id": draft.wash_crew_role_id,
                "watch_party_role_id": draft.watch_party_role_id,
                "watch_party_join_mode": draft.watch_party_join_mode.value if draft.watch_party_join_mode else None,
                "suggestion_database_id": draft.suggestion_database_id,
                "suggestion_database_name": draft.suggestion_database_name,
                "suggestion_database_is_new": draft.suggestion_database_is_new,
                "watch_destination_channel_id": draft.watch_destination_channel_id,
                "watch_destination_skipped": draft.watch_destination_skipped,
                "voting_candidate_count": draft.voting_candidate_count,
                "voting_duration_days": draft.voting_duration_days,
                "voting_visibility": draft.voting_visibility.value if draft.voting_visibility else None,
                "voting_candidate_selection": (
                    draft.voting_candidate_selection.value if draft.voting_candidate_selection else None
                ),
                "reminder_enabled": draft.reminder_enabled,
                "reminder_hours_before_close": draft.reminder_hours_before_close,
                "backup_interval_days": draft.backup_interval_days,
                "backup_retention_count": draft.backup_retention_count,
            },
        }

    @staticmethod
    def _deserialize(entry: dict[str, Any]) -> SetupWizardState:
        draft_entry = entry.get("draft") or {}
        join_mode_raw = draft_entry.get("watch_party_join_mode")
        visibility_raw = draft_entry.get("voting_visibility")
        candidate_selection_raw = draft_entry.get("voting_candidate_selection")

        draft = SetupWizardDraft(
            wash_crew_role_id=draft_entry.get("wash_crew_role_id"),
            watch_party_role_id=draft_entry.get("watch_party_role_id"),
            watch_party_join_mode=JoinMode(join_mode_raw) if join_mode_raw else None,
            suggestion_database_id=draft_entry.get("suggestion_database_id"),
            suggestion_database_name=draft_entry.get("suggestion_database_name"),
            suggestion_database_is_new=draft_entry.get("suggestion_database_is_new", False),
            watch_destination_channel_id=draft_entry.get("watch_destination_channel_id"),
            watch_destination_skipped=draft_entry.get("watch_destination_skipped", False),
            voting_candidate_count=draft_entry.get("voting_candidate_count"),
            voting_duration_days=draft_entry.get("voting_duration_days"),
            voting_visibility=GuildVoteVisibility(visibility_raw) if visibility_raw else None,
            voting_candidate_selection=(
                CandidateSelectionMode(candidate_selection_raw) if candidate_selection_raw else None
            ),
            reminder_enabled=draft_entry.get("reminder_enabled"),
            reminder_hours_before_close=draft_entry.get("reminder_hours_before_close"),
            backup_interval_days=draft_entry.get("backup_interval_days"),
            backup_retention_count=draft_entry.get("backup_retention_count"),
        )

        return SetupWizardState(
            guild_id=entry["guild_id"],
            status=SetupWizardStatus(entry.get("status", "in_progress")),
            current_step=SetupWizardStep(entry.get("current_step", SetupWizardStep.WASH_CREW_ROLE.value)),
            completed_steps=tuple(SetupWizardStep(value) for value in entry.get("completed_steps", [])),
            draft=draft,
            started_at=datetime.fromisoformat(entry["started_at"]),
            updated_at=datetime.fromisoformat(entry["updated_at"]),
        )
