from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from watch_party_manager.bot import WatchPartyBot
from watch_party_manager.scheduler import CLOSE_VOTE_JOB_TYPE, VOTE_REMINDER_JOB_TYPE


class WatchPartyBotSchedulerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_stops_scheduler_before_discord_shutdown(self) -> None:
        bot = WatchPartyBot(token="test-token")
        bot.scheduler_host.stop = AsyncMock()

        await bot.close()

        bot.scheduler_host.stop.assert_awaited_once_with()

    def test_registers_a_close_vote_handler_during_construction(self) -> None:
        bot = WatchPartyBot(token="test-token")

        # register_handler() rejects a second registration for the same
        # job type (see SchedulerService), so this confirms one was
        # already registered by WatchPartyBot.__init__ without reaching
        # into SchedulerService's private handler map.
        with self.assertRaises(ValueError):
            bot.scheduler_host.scheduler_service.register_handler(CLOSE_VOTE_JOB_TYPE, object())

    def test_registers_a_vote_reminder_handler_during_construction(self) -> None:
        bot = WatchPartyBot(token="test-token")

        with self.assertRaises(ValueError):
            bot.scheduler_host.scheduler_service.register_handler(VOTE_REMINDER_JOB_TYPE, object())


if __name__ == "__main__":
    unittest.main()
