"""JSON persistence for scheduled jobs."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .scheduled_job import JobResult, JobStatus, ScheduledJob


class DuplicateActiveJobError(ValueError):
    """Raised when an active job already uses the same logical key."""


class ScheduledJobNotFoundError(KeyError):
    """Raised when a requested scheduled job does not exist."""


class InvalidSchedulerDataError(ValueError):
    """Raised when persisted scheduler data cannot be read safely."""


class JsonSchedulerRepository:
    """Persist scheduled jobs in one versioned JSON document.

    File access is guarded within the current process. Writes use a temporary
    file followed by os.replace so readers never observe a partially written
    document.
    """

    SCHEMA_VERSION = 1

    def __init__(self, file_path: str | Path) -> None:
        self._file_path = Path(file_path)
        self._lock = asyncio.Lock()

    @property
    def file_path(self) -> Path:
        return self._file_path

    async def add(self, job: ScheduledJob) -> ScheduledJob:
        async with self._lock:
            jobs = self._load_jobs()
            duplicate = self._find_active_by_logical_key(jobs, job.logical_key)
            if duplicate is not None:
                raise DuplicateActiveJobError(
                    f"active job already exists for logical key: {job.logical_key}"
                )
            if any(existing.job_id == job.job_id for existing in jobs):
                raise ValueError(f"job_id already exists: {job.job_id}")

            jobs.append(job)
            self._save_jobs(jobs)
            return job

    async def get_due(
        self,
        now: datetime,
        *,
        limit: int = 100,
    ) -> list[ScheduledJob]:
        if limit <= 0:
            raise ValueError("limit must be positive")

        async with self._lock:
            jobs = self._load_jobs()
            due = [
                job
                for job in jobs
                if job.status is JobStatus.PENDING and job.run_at <= now
            ]
            due.sort(key=lambda job: (job.run_at, job.created_at, job.job_id))
            return due[:limit]

    async def claim(
        self,
        job_id: str,
        started_at: datetime,
    ) -> ScheduledJob | None:
        async with self._lock:
            jobs = self._load_jobs()
            index = self._find_index(jobs, job_id)
            if index is None:
                raise ScheduledJobNotFoundError(job_id)

            job = jobs[index]
            if job.status is not JobStatus.PENDING:
                return None

            claimed = job.with_changes(
                status=JobStatus.RUNNING,
                started_at=started_at,
                attempt_count=job.attempt_count + 1,
            )
            jobs[index] = claimed
            self._save_jobs(jobs)
            return claimed

    async def complete(
        self,
        job_id: str,
        completed_at: datetime,
        result: JobResult,
    ) -> ScheduledJob:
        return await self._replace_status(
            job_id,
            status=JobStatus.COMPLETED,
            completed_at=completed_at,
            result=result,
            last_error=None,
        )

    async def retry(
        self,
        job_id: str,
        run_at: datetime,
        error: str,
    ) -> ScheduledJob:
        return await self._replace_status(
            job_id,
            status=JobStatus.PENDING,
            run_at=run_at,
            last_error=error,
            completed_at=None,
            result=None,
        )

    async def fail(
        self,
        job_id: str,
        completed_at: datetime,
        error: str,
    ) -> ScheduledJob:
        return await self._replace_status(
            job_id,
            status=JobStatus.FAILED,
            completed_at=completed_at,
            last_error=error,
            result=None,
        )

    async def cancel(
        self,
        job_id: str,
        completed_at: datetime,
    ) -> ScheduledJob:
        async with self._lock:
            jobs = self._load_jobs()
            index = self._find_index(jobs, job_id)
            if index is None:
                raise ScheduledJobNotFoundError(job_id)

            job = jobs[index]
            if job.status not in {JobStatus.PENDING, JobStatus.RUNNING}:
                return job

            cancelled = job.with_changes(
                status=JobStatus.CANCELLED,
                completed_at=completed_at,
                result=JobResult.CANCELLED,
            )
            jobs[index] = cancelled
            self._save_jobs(jobs)
            return cancelled

    async def find_active_by_logical_key(
        self,
        logical_key: str,
    ) -> ScheduledJob | None:
        async with self._lock:
            return self._find_active_by_logical_key(
                self._load_jobs(),
                logical_key,
            )

    async def get_by_id(self, job_id: str) -> ScheduledJob | None:
        async with self._lock:
            jobs = self._load_jobs()
            index = self._find_index(jobs, job_id)
            return None if index is None else jobs[index]

    async def list_all(self) -> list[ScheduledJob]:
        async with self._lock:
            return list(self._load_jobs())

    async def remove_for_guild(self, guild_id: int) -> list[ScheduledJob]:
        """Permanently remove every job belonging to one guild.

        Unlike cancel(), which soft-marks a single job CANCELLED and
        keeps its record, this hard-deletes -- used by FR-032C's
        factory reset, where stale job records (even cancelled ones)
        should not survive a reset.

        Returns:
            The removed jobs, for reporting purposes.
        """
        async with self._lock:
            jobs = self._load_jobs()
            removed = [job for job in jobs if job.guild_id == guild_id]
            if removed:
                remaining = [job for job in jobs if job.guild_id != guild_id]
                self._save_jobs(remaining)
            return removed

    async def _replace_status(
        self,
        job_id: str,
        **changes: Any,
    ) -> ScheduledJob:
        async with self._lock:
            jobs = self._load_jobs()
            index = self._find_index(jobs, job_id)
            if index is None:
                raise ScheduledJobNotFoundError(job_id)

            updated = jobs[index].with_changes(**changes)
            jobs[index] = updated
            self._save_jobs(jobs)
            return updated

    def _load_jobs(self) -> list[ScheduledJob]:
        if not self._file_path.exists():
            return []

        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidSchedulerDataError(
                f"unable to read scheduler data: {self._file_path}"
            ) from exc

        if not isinstance(raw, dict):
            raise InvalidSchedulerDataError("scheduler data root must be an object")
        if raw.get("schema_version") != self.SCHEMA_VERSION:
            raise InvalidSchedulerDataError(
                f"unsupported scheduler schema version: {raw.get('schema_version')!r}"
            )

        records = raw.get("jobs")
        if not isinstance(records, list):
            raise InvalidSchedulerDataError("scheduler jobs must be a list")

        try:
            return [self._deserialize_job(record) for record in records]
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidSchedulerDataError("scheduler contains an invalid job") from exc

    def _save_jobs(self, jobs: list[ScheduledJob]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": self.SCHEMA_VERSION,
            "jobs": [self._serialize_job(job) for job in jobs],
        }

        temp_name: str | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._file_path.parent,
                prefix=f".{self._file_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                json.dump(document, temp_file, indent=2, sort_keys=True)
                temp_file.write("\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_name = temp_file.name

            os.replace(temp_name, self._file_path)
        finally:
            if temp_name is not None and os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _find_index(jobs: list[ScheduledJob], job_id: str) -> int | None:
        return next(
            (index for index, job in enumerate(jobs) if job.job_id == job_id),
            None,
        )

    @staticmethod
    def _find_active_by_logical_key(
        jobs: list[ScheduledJob],
        logical_key: str,
    ) -> ScheduledJob | None:
        return next(
            (
                job
                for job in jobs
                if job.logical_key == logical_key and job.is_active
            ),
            None,
        )

    @staticmethod
    def _serialize_job(job: ScheduledJob) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "guild_id": job.guild_id,
            "job_type": job.job_type,
            "logical_key": job.logical_key,
            "run_at": job.run_at.isoformat(),
            "status": job.status.value,
            "payload": job.payload,
            "created_at": job.created_at.isoformat(),
            "started_at": (
                None if job.started_at is None else job.started_at.isoformat()
            ),
            "completed_at": (
                None if job.completed_at is None else job.completed_at.isoformat()
            ),
            "attempt_count": job.attempt_count,
            "last_error": job.last_error,
            "result": None if job.result is None else job.result.value,
        }

    @staticmethod
    def _deserialize_job(record: dict[str, Any]) -> ScheduledJob:
        if not isinstance(record, dict):
            raise TypeError("job record must be an object")

        return ScheduledJob(
            job_id=str(record["job_id"]),
            guild_id=int(record["guild_id"]),
            job_type=str(record["job_type"]),
            logical_key=str(record["logical_key"]),
            run_at=datetime.fromisoformat(record["run_at"]),
            status=JobStatus(record["status"]),
            payload=dict(record.get("payload", {})),
            created_at=datetime.fromisoformat(record["created_at"]),
            started_at=(
                None
                if record.get("started_at") is None
                else datetime.fromisoformat(record["started_at"])
            ),
            completed_at=(
                None
                if record.get("completed_at") is None
                else datetime.fromisoformat(record["completed_at"])
            ),
            attempt_count=int(record.get("attempt_count", 0)),
            last_error=record.get("last_error"),
            result=(
                None
                if record.get("result") is None
                else JobResult(record["result"])
            ),
        )
