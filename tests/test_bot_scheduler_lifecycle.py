from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

import watch_party_manager.bot as bot_module
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


class LegacyPollingWorkflowRetiredTests(unittest.TestCase):
    """FR-019: the old bot.py background task that independently polled
    for and completed expired voting rounds is gone -- scheduled
    close_vote jobs (via CloseVoteJobHandler) are the only automatic
    completion path left.
    """

    def test_no_legacy_polling_task_attribute_remains(self) -> None:
        bot = WatchPartyBot(token="test-token")

        self.assertFalse(hasattr(bot, "check_expired_votes_task"))

    def test_legacy_completion_functions_are_no_longer_exported(self) -> None:
        self.assertFalse(hasattr(bot_module, "check_and_announce_expired_vote"))
        self.assertFalse(hasattr(bot_module, "perform_vote_completion_check"))

    def test_legacy_poll_interval_constant_is_gone(self) -> None:
        self.assertFalse(hasattr(bot_module, "VOTE_EXPIRATION_CHECK_INTERVAL_SECONDS"))


if __name__ == "__main__":
    unittest.main()
