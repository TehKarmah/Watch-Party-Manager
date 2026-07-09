from __future__ import annotations

import os
from typing import Optional

import discord
from discord.ext import commands

from watch_party_manager.version import __version__


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

    async def start_bot(self) -> None:
        if not self.token:
            raise RuntimeError("DISCORD_TOKEN environment variable is required")
        await super().start(self.token)


def build_help_text() -> str:
    return "Available commands:\n- /ping\n- /version\n- /help"


def build_version_text(version: str) -> str:
    return f"Watch Party Manager version {version}"


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    bot = WatchPartyBot(token=token)

    import asyncio

    asyncio.run(bot.start_bot())


if __name__ == "__main__":
    main()
