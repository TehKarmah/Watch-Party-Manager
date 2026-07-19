from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from watch_party_manager.bot import WatchPartyBot


class WatchPartyBotSchedulerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_stops_scheduler_before_discord_shutdown(self) -> None:
        bot = WatchPartyBot(token="test-token")
        bot.scheduler_host.stop = AsyncMock()

        await bot.close()

        bot.scheduler_host.stop.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
