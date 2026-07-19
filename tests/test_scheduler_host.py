from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from watch_party_manager.scheduler.json_scheduler_repository import (
    JsonSchedulerRepository,
)
from watch_party_manager.scheduler.scheduler_host import SchedulerHost
from watch_party_manager.scheduler.scheduler_service import SchedulerService


class RecordingSchedulerService:
    def __init__(self) -> None:
        self.poll_interval_seconds = 60
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.run_count = 0

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        self.run_count += 1
        self.started.set()
        await stop_event.wait()
        self.stopped.set()


class FailingSchedulerService:
    poll_interval_seconds = 60

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        raise RuntimeError("scheduler failure")


class SchedulerHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_runs_scheduler_in_background(self) -> None:
        service = RecordingSchedulerService()
        host = SchedulerHost(service)

        await host.start()
        await asyncio.wait_for(service.started.wait(), timeout=1)

        self.assertTrue(host.is_running)
        self.assertEqual(service.run_count, 1)
        await host.stop()

    async def test_start_is_idempotent(self) -> None:
        service = RecordingSchedulerService()
        host = SchedulerHost(service)

        await host.start()
        await host.start()
        await asyncio.wait_for(service.started.wait(), timeout=1)

        self.assertEqual(service.run_count, 1)
        await host.stop()

    async def test_stop_signals_and_awaits_scheduler(self) -> None:
        service = RecordingSchedulerService()
        host = SchedulerHost(service)
        await host.start()
        await asyncio.wait_for(service.started.wait(), timeout=1)

        await host.stop()

        self.assertTrue(service.stopped.is_set())
        self.assertFalse(host.is_running)

    async def test_stop_before_start_is_safe(self) -> None:
        host = SchedulerHost(RecordingSchedulerService())

        await host.stop()

        self.assertFalse(host.is_running)

    async def test_host_can_restart_after_clean_stop(self) -> None:
        service = RecordingSchedulerService()
        host = SchedulerHost(service)

        await host.start()
        await asyncio.wait_for(service.started.wait(), timeout=1)
        await host.stop()
        service.started.clear()

        await host.start()
        await asyncio.wait_for(service.started.wait(), timeout=1)

        self.assertEqual(service.run_count, 2)
        await host.stop()

    async def test_from_json_file_builds_concrete_runtime(self) -> None:
        with TemporaryDirectory() as directory:
            file_path = Path(directory) / "scheduled_jobs.json"

            host = SchedulerHost.from_json_file(file_path)

            self.assertIsInstance(host.scheduler_service, SchedulerService)
            self.assertIsInstance(
                host.scheduler_service._repository,
                JsonSchedulerRepository,
            )
            self.assertEqual(
                host.scheduler_service._repository.file_path,
                file_path,
            )

    async def test_unexpected_failure_leaves_host_not_running(self) -> None:
        host = SchedulerHost(FailingSchedulerService())

        await host.start()
        await asyncio.sleep(0)

        self.assertFalse(host.is_running)


if __name__ == "__main__":
    unittest.main()
