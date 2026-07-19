"""Lifecycle management for WASH's background scheduler."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .json_scheduler_repository import JsonSchedulerRepository
from .scheduler_service import SchedulerService


class SchedulerHost:
    """Own the scheduler service's background task and shutdown signal."""

    def __init__(
        self,
        scheduler_service: SchedulerService,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.scheduler_service = scheduler_service
        self._logger = logger or logging.getLogger(__name__)
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    @classmethod
    def from_json_file(
        cls,
        file_path: str | Path,
        *,
        poll_interval_seconds: int = 60,
        logger: logging.Logger | None = None,
    ) -> "SchedulerHost":
        repository = JsonSchedulerRepository(file_path)
        service = SchedulerService(
            repository,
            poll_interval_seconds=poll_interval_seconds,
            logger=logger,
        )
        return cls(service, logger=logger)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start the scheduler loop once.

        Repeated calls are safe and do not create duplicate tasks.
        """
        if self.is_running:
            return

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self.scheduler_service.run_forever(self._stop_event),
            name="wash-scheduler",
        )
        self._task.add_done_callback(self._log_unexpected_completion)
        self._logger.info(
            "Scheduler started with a %s-second polling interval",
            self.scheduler_service.poll_interval_seconds,
        )

    async def stop(self) -> None:
        """Request shutdown and wait for the scheduler task to finish."""
        task = self._task
        stop_event = self._stop_event
        if task is None:
            return

        if stop_event is not None:
            stop_event.set()

        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            self._stop_event = None

        self._logger.info("Scheduler stopped")

    def _log_unexpected_completion(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return

        error = task.exception()
        if error is not None:
            self._logger.error(
                "Scheduler stopped unexpectedly",
                exc_info=(type(error), error, error.__traceback__),
            )
