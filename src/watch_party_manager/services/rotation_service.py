"""Service for FR-033B's rotation lifecycle, admission, and progress tracking.

Kept entirely separate from VoteService and NomineeSelectionService per
FR-033B instruction #2 ("Do not hard-code behavior into VoteService"):
this owns rotation bookkeeping only. candidate_selection_strategy.py is
the layer that consumes it to decide which suggestions are selectable
and how heavily weighted, from NomineeSelectionService.

Presentation tracking is derived, not duplicated: whether a suggestion
has been presented within a given rotation is determined by checking
whether that rotation's id appears in the suggestion's own
WatchItemJourney.rotation_history (see domain/watch_item_journey.py),
which is itself a pre-existing, previously-dormant field this milestone
is the first to populate. This keeps a single source of truth instead of
tracking "presented" twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol, Sequence

from watch_party_manager.domain.rotation import Rotation, RotationStatus
from watch_party_manager.domain.suggestion_database_configuration import SuggestionAdmissionMode
from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository


class RotationSuggestionSource(Protocol):
    """The subset of SuggestionService RotationService needs.

    Kept as a small Protocol, matching this project's existing
    SuggestionLookup/SuggestionSource conventions, so RotationService
    depends only on the capabilities it actually uses and tests can
    supply a lightweight fake.
    """

    def get_suggestions_for_database(self, database_id: int) -> List[WatchItem]: ...

    def get_suggestion(self, suggestion_id: int) -> Optional[WatchItem]: ...

    def record_rotation_presentation(self, suggestion_id: int, rotation_id: int) -> bool: ...


@dataclass(frozen=True)
class RotationProgress:
    """Snapshot of one rotation's completion state (FR-033B Section 6)."""

    total: int
    presented: int
    remaining: int
    retired: int
    watched: int
    completion_percentage: float


class RotationService:
    """Manages Rotation records: lifecycle, admission, and progress.

    Only one rotation may be OPEN per database at a time -- mirrors
    VoteService's "only one round may be open at a time" convention, one
    level down (per database rather than global).
    """

    def __init__(
        self,
        suggestion_source: RotationSuggestionSource,
        repository: Optional[JsonRotationRepository] = None,
    ) -> None:
        self._suggestion_source = suggestion_source
        self._repository = repository if repository is not None else JsonRotationRepository()
        load_result = self._repository.load()
        self._rotations: Dict[int, Rotation] = {rotation.id: rotation for rotation in load_result.rotations}
        self._next_id = load_result.next_rotation_id
        self._low_pool_reminder_last_sent_at: Dict[int, datetime] = dict(
            load_result.low_pool_reminder_last_sent_at
        )

    # --- Lifecycle -----------------------------------------------------------------

    def get_open_rotation(self, database_id: int) -> Optional[Rotation]:
        """Return the currently open rotation for a database, if any (no bootstrap)."""
        for rotation in self._rotations.values():
            if rotation.database_id == database_id and rotation.status is RotationStatus.OPEN:
                return rotation
        return None

    def get_or_start_rotation(self, database_id: int) -> Rotation:
        """Return the open rotation for a database, starting a fresh one if none exists.

        A freshly started rotation is assigned every currently eligible
        (non-archived) suggestion in the database -- see _begin_rotation.
        """
        existing = self.get_open_rotation(database_id)
        if existing is not None:
            return existing
        return self._begin_rotation(database_id)

    def begin_next_rotation(self, database_id: int) -> Rotation:
        """Complete the current open rotation (if any) and start a fresh one.

        A fresh rotation reassigns every currently eligible suggestion,
        including ones presented in the outgoing rotation -- "when the
        rotation is exhausted, begin a fresh rotation" (FR-033B Section 1).
        """
        current = self.get_open_rotation(database_id)
        if current is not None:
            self._complete_rotation(current)
        return self._begin_rotation(database_id)

    def current_rotation_for_selection(self, database_id: int) -> Rotation:
        """Return the rotation to use for an upcoming selection.

        Bootstraps a rotation if none is open, and automatically begins a
        fresh one if the open rotation is already exhausted (every
        assigned suggestion has reached a terminal state) -- this is the
        "automatic new rotation" behavior FR-033B Section 1 describes.
        """
        rotation = self.get_or_start_rotation(database_id)
        if self._is_exhausted(rotation):
            rotation = self.begin_next_rotation(database_id)
        return rotation

    def _begin_rotation(self, database_id: int) -> Rotation:
        eligible_ids = [
            item.id
            for item in self._suggestion_source.get_suggestions_for_database(database_id)
            if item.id is not None
        ]
        rotation = Rotation(id=self._next_id, database_id=database_id, assigned_suggestion_ids=tuple(eligible_ids))
        self._next_id += 1
        self._rotations[rotation.id] = rotation
        self._save()
        return rotation

    def _complete_rotation(self, rotation: Rotation) -> None:
        rotation.status = RotationStatus.COMPLETED
        rotation.completed_at = datetime.now(timezone.utc)
        self._save()

    # --- Admission -------------------------------------------------------------------

    def admit_suggestion(
        self, database_id: int, suggestion_id: int, admission_mode: SuggestionAdmissionMode
    ) -> None:
        """Apply FR-033B Section 5's admission rule for a newly created/reactivated suggestion.

        NEXT_ROTATION (the default) is a deliberate no-op: the suggestion
        is picked up automatically the next time a rotation is (re)started
        for this database, whether that's the first rotation ever or the
        next fresh one after exhaustion. JOIN_CURRENT_ROTATION immediately
        assigns it to whichever rotation is currently open (bootstrapping
        one first if none exists yet).
        """
        if admission_mode is not SuggestionAdmissionMode.JOIN_CURRENT_ROTATION:
            return
        rotation = self.get_or_start_rotation(database_id)
        if suggestion_id not in rotation.assigned_suggestion_ids:
            rotation.assigned_suggestion_ids = (*rotation.assigned_suggestion_ids, suggestion_id)
            self._save()

    # --- Presentation ------------------------------------------------------------------

    def record_presentation(self, database_id: int, suggestion_ids: Sequence[int]) -> Rotation:
        """Mark suggestions as presented within the database's current rotation.

        Ensures a rotation exists first (bootstrapping one if needed),
        then records this rotation's id onto each presented suggestion's
        journey.rotation_history via the suggestion source -- the single
        source of truth for "has this item been presented in this
        rotation" (see module docstring).
        """
        rotation = self.get_or_start_rotation(database_id)
        for suggestion_id in suggestion_ids:
            self._suggestion_source.record_rotation_presentation(suggestion_id, rotation.id)
        return rotation

    # --- Reporting ----------------------------------------------------------------------

    def rotation_progress(self, database_id: int) -> RotationProgress:
        """Report the current rotation's completion state (FR-033B Section 6).

        Read-only: bootstraps a rotation if none exists yet (so progress
        is always available once a database has suggestions), but never
        auto-completes/restarts an exhausted one as a side effect of
        merely checking progress -- that's current_rotation_for_selection's
        job, invoked deliberately during selection.
        """
        rotation = self.get_or_start_rotation(database_id)
        return self._progress_for(rotation)

    def progress_for_rotation(self, rotation: Rotation) -> RotationProgress:
        """Compute progress for an already-known rotation, without any bootstrap.

        Public counterpart to rotation_progress() for callers (like
        FR-034 statistics) that already have a specific Rotation object
        in hand -- e.g. from list_rotations() -- and must never trigger
        get_or_start_rotation's side effect of creating one.
        """
        return self._progress_for(rotation)

    def list_rotations(self, database_id: int) -> List[Rotation]:
        """Return every rotation ever recorded for a database, oldest first.

        Read-only, no bootstrap -- an Infinite Pool database (or one that
        simply never started a rotation yet) correctly returns an empty
        list rather than having one created for it.
        """
        return sorted(
            (rotation for rotation in self._rotations.values() if rotation.database_id == database_id),
            key=lambda rotation: rotation.id,
        )

    def remaining_suggestions(self, database_id: int) -> List[WatchItem]:
        """Return the current rotation's not-yet-terminal assigned suggestions."""
        rotation = self.get_or_start_rotation(database_id)
        remaining: List[WatchItem] = []
        for suggestion_id in rotation.assigned_suggestion_ids:
            item = self._suggestion_source.get_suggestion(suggestion_id)
            if item is not None and self._classify(item, rotation.id) == "pending":
                remaining.append(item)
        return remaining

    def is_candidate_eligible(self, watch_item: WatchItem, database_id: int) -> bool:
        """Return whether a suggestion is Rotation-Pool-eligible right now.

        True only for suggestions assigned to the current rotation and
        not yet presented within it, and not in any other terminal state.
        """
        rotation = self.get_or_start_rotation(database_id)
        if watch_item.id not in rotation.assigned_suggestion_ids:
            return False
        return self._classify(watch_item, rotation.id) == "pending"

    def _progress_for(self, rotation: Rotation) -> RotationProgress:
        presented = watched = retired = 0
        for suggestion_id in rotation.assigned_suggestion_ids:
            item = self._suggestion_source.get_suggestion(suggestion_id)
            classification = self._classify(item, rotation.id)
            if classification == "presented":
                presented += 1
            elif classification == "watched":
                watched += 1
            elif classification == "retired":
                retired += 1

        total = len(rotation.assigned_suggestion_ids)
        pending = sum(
            1
            for suggestion_id in rotation.assigned_suggestion_ids
            if self._classify(self._suggestion_source.get_suggestion(suggestion_id), rotation.id) == "pending"
        )
        completion_percentage = 0.0 if total == 0 else round((total - pending) / total * 100, 2)
        return RotationProgress(
            total=total,
            presented=presented,
            remaining=pending,
            retired=retired,
            watched=watched,
            completion_percentage=completion_percentage,
        )

    @staticmethod
    def _classify(item: Optional[WatchItem], rotation_id: int) -> str:
        """Classify one assigned suggestion for progress/exhaustion purposes.

        Returns one of: "removed", "watched", "retired", "archived",
        "presented", or "pending" -- pending is the only non-terminal
        state (see _is_exhausted).
        """
        if item is None:
            return "removed"
        if item.status is WatchItemStatus.WATCHED:
            return "watched"
        if item.status is WatchItemStatus.ARCHIVED:
            return "retired" if item.journey.retired_at is not None else "archived"
        if rotation_id in item.journey.rotation_history:
            return "presented"
        return "pending"

    def _is_exhausted(self, rotation: Rotation) -> bool:
        if not rotation.assigned_suggestion_ids:
            return False
        for suggestion_id in rotation.assigned_suggestion_ids:
            item = self._suggestion_source.get_suggestion(suggestion_id)
            if self._classify(item, rotation.id) == "pending":
                return False
        return True

    # --- Low Pool Reminder interval tracking (FR-033B Section 7) --------------------------

    def last_low_pool_reminder_sent_at(self, database_id: int) -> Optional[datetime]:
        return self._low_pool_reminder_last_sent_at.get(database_id)

    def record_low_pool_reminder_sent(self, database_id: int, sent_at: datetime) -> None:
        self._low_pool_reminder_last_sent_at[database_id] = sent_at
        self._save()

    # --- Persistence ----------------------------------------------------------------------

    def _save(self) -> None:
        self._repository.save(
            list(self._rotations.values()), self._next_id, dict(self._low_pool_reminder_last_sent_at)
        )
