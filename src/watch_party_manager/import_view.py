"""Discord UI for FR-032C's /import mode choice.

Merge and Replace get deliberately different weights of confirmation,
per the milestone's own spec: Merge never overwrites an existing
record (skips conflicts instead), so a single explicit click is its
confirmation. Replace can overwrite this guild's entire portable
dataset, so it additionally requires typing "REPLACE" -- reusing
type_to_confirm_view.py's existing modal/button pair rather than
duplicating that safeguard.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord

from watch_party_manager.type_to_confirm_view import (
    CancelDestructiveActionButton,
    OnDestructiveCancelled,
    OnTypedConfirm,
    OpenTypeToConfirmButton,
)

OnMergeConfirmed = Callable[[discord.Interaction], Awaitable[None]]

IMPORT_MODE_CHOICE_TIMEOUT_SECONDS = 60
IMPORT_REPLACE_REQUIRED_TEXT = "REPLACE"


class MergeImportButton(discord.ui.Button):
    """Merge is non-destructive by design, so clicking it is itself the confirmation."""

    def __init__(self, on_confirm: OnMergeConfirmed) -> None:
        super().__init__(label="Merge", style=discord.ButtonStyle.primary, custom_id="wpm_import_merge")
        self._on_confirm = on_confirm

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_confirm(interaction)


class ImportModeChoiceView(discord.ui.View):
    """The post-validation "Merge, Replace, or Cancel" prompt for /import."""

    def __init__(
        self,
        *,
        on_merge: OnMergeConfirmed,
        on_replace: OnTypedConfirm,
        on_cancel: OnDestructiveCancelled,
    ) -> None:
        super().__init__(timeout=IMPORT_MODE_CHOICE_TIMEOUT_SECONDS)
        self.add_item(MergeImportButton(on_merge))
        self.add_item(
            OpenTypeToConfirmButton(
                label="Replace",
                custom_id="wpm_import_replace_open",
                required_text=IMPORT_REPLACE_REQUIRED_TEXT,
                modal_title="Replace Import",
                on_confirm=on_replace,
            )
        )
        self.add_item(CancelDestructiveActionButton(custom_id="wpm_import_cancel", on_cancel=on_cancel))
