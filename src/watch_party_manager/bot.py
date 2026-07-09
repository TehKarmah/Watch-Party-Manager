from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv

from watch_party_manager.version import __version__

logger = logging.getLogger(__name__)


class WatchPartyBot(commands.Bot):
    """A minimal Discord bot for the initial vertical slice."""

    def __init__(self, *, token: Optional[str] = None) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.token = token

    async def setup_hook(self) -> None:
        @self.tree.command(name="ping")
        async def ping(interaction: discord.Interaction) -> None:
            await interaction.response.send_message("Pong.")

        @self.tree.command(name="version")
        async def version(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(build_version_text(__version__))

        @self.tree.command(name="help")
        async def help_command(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(build_help_text())

        logger.info("Synchronizing slash commands...")
        synced = await self.tree.sync()
        logger.info(f"Synchronized {len(synced)} command(s)")

    async def on_ready(self) -> None:
        logger.info(f"Logged in as {self.user}")
        logger.info("Ready")

    async def start_bot(self) -> None:
        if not self.token:
            logger.error("DISCORD_TOKEN environment variable is required. Please set it in .env or your environment.")
            raise RuntimeError("DISCORD_TOKEN environment variable is required")
        try:
            logger.info("Starting HAL...")
            await super().start(self.token)
        except discord.errors.LoginFailure:
            logger.error("Failed to login. Invalid DISCORD_TOKEN or bot token has been revoked.")
            raise


def build_help_text() -> str:
    return "Available commands:\n- /ping\n- /version\n- /help"


def build_version_text(version: str) -> str:
    return f"Watch Party Manager version {version}"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    bot = WatchPartyBot(token=token)

    try:
        asyncio.run(bot.start_bot())
    except RuntimeError as e:
        logger.error(f"Failed to start bot: {e}")
        exit(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
