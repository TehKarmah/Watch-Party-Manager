from __future__ import annotations

from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parent
BOT_PATH = ROOT / "src" / "watch_party_manager" / "bot.py"
BACKUP_PATH = ROOT / "src" / "watch_party_manager" / "bot.py.before_scheduler_merge"


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count == 0:
        raise RuntimeError(f"Could not find insertion point for {description}.")
    if count > 1:
        raise RuntimeError(
            f"Found {count} possible insertion points for {description}; "
            "refusing to make an ambiguous edit."
        )
    return text.replace(old, new, 1)


def main() -> int:
    if not BOT_PATH.exists():
        print(f"ERROR: bot.py not found at {BOT_PATH}", file=sys.stderr)
        return 1

    original = BOT_PATH.read_text(encoding="utf-8")
    updated = original

    if "from pathlib import Path\n" not in updated:
        updated = replace_once(
            updated,
            "from datetime import datetime, timedelta, timezone\n",
            "from datetime import datetime, timedelta, timezone\n"
            "from pathlib import Path\n",
            "Path import",
        )

    if "from watch_party_manager.scheduler import SchedulerHost\n" not in updated:
        updated = replace_once(
            updated,
            "from watch_party_manager.logger_config import configure_logging\n",
            "from watch_party_manager.logger_config import configure_logging\n"
            "from watch_party_manager.scheduler import SchedulerHost\n",
            "SchedulerHost import",
        )

    scheduler_assignment = (
        "        self.scheduler_host = SchedulerHost.from_json_file(\n"
        '            Path("data") / "scheduled_jobs.json"\n'
        "        )\n"
    )
    if "self.scheduler_host = SchedulerHost.from_json_file(" not in updated:
        updated = replace_once(
            updated,
            "        self.interactive_voting_restored = False\n",
            "        self.interactive_voting_restored = False\n"
            + scheduler_assignment,
            "scheduler host initialization",
        )

    if "await self.scheduler_host.start()" not in updated:
        global_sync_block = (
            '            logger.info("Synchronizing slash commands globally...")\n'
            "            synced = await self.tree.sync()\n"
            '            logger.info(f"Synchronized {len(synced)} command(s) globally")\n'
        )
        updated = replace_once(
            updated,
            global_sync_block,
            global_sync_block + "\n        await self.scheduler_host.start()\n",
            "scheduler startup",
        )

    close_method = (
        "\n    async def close(self) -> None:\n"
        "        await self.scheduler_host.stop()\n"
        "        await super().close()\n"
    )
    if "await self.scheduler_host.stop()" not in updated:
        updated = replace_once(
            updated,
            "\n    async def on_ready(self) -> None:\n",
            close_method + "\n    async def on_ready(self) -> None:\n",
            "scheduler shutdown",
        )

    if updated == original:
        print("No changes needed. bot.py already contains the scheduler lifecycle integration.")
        return 0

    shutil.copy2(BOT_PATH, BACKUP_PATH)
    BOT_PATH.write_text(updated, encoding="utf-8")

    print(f"Updated: {BOT_PATH}")
    print(f"Backup:  {BACKUP_PATH}")
    print("Scheduler lifecycle integration completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
