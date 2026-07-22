"""Shared "type X to confirm" Discord UI for FR-032C's destructive operations.

Suggestion database reset, factory reset, and Replace-mode import all
require the same safeguard: WASH Crew must type an exact, case-sensitive
confirmation phrase (e.g. "RESET" or "REPLACE") before anything
destructive happens. Mirrors edit_vote_view.py's Modal/Button pattern
and restore_confirmation_view.py's confirm/cancel View shape -- this
module only adds the "must type X exactly" layer on top of that
existing shape, reused by every destructive workflow in this milestone
instead of being duplicated three times.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord

OnTypedConfirm = Callable[[discord.Interaction], Awaitable[None]]
OnDestructiveCancelled = Callable[[discord.Interaction], Awaitable[None]]

DESTRUCTIVE_CONFIRMATION_TIMEOUT_SECONDS = 60


class TypeToConfirmModal(discord.ui.Modal):
    """Requires the exact, case-sensitive confirmation phrase before proceeding.

    Never performs the action itself -- a mismatch reports "no changes
    were made" and stops; only an exact match forwards to on_confirm.
    """

    def __init__(self, *, title: str, required_text: str, on_confirm: OnTypedConfirm) -> None:
        super().__init__(title=title)
        self._required_text = required_text
        self._on_confirm = on_confirm
        self.confirmation_input = discord.ui.TextInput(
            label=f'Type "{required_text}" to confirm',
            placeholder=required_text,
            required=True,
            max_length=len(required_text) + 20,
        )
        self.add_item(self.confirmation_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.confirmation_input.value != self._required_text:
            await interaction.response.send_message(
                f'Confirmation text did not match "{self._required_text}" exactly '
                "(this check is case-sensitive). No changes were made.",
                ephemeral=True,
            )
            return
        await self._on_confirm(interaction)


class OpenTypeToConfirmButton(discord.ui.Button):
    """Opens the typed-confirmation modal instead of acting immediately."""

    def __init__(
        self,
        *,
        label: str,
        custom_id: str,
        required_text: str,
        modal_title: str,
        on_confirm: OnTypedConfirm,
    ) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.danger, custom_id=custom_id)
        self._required_text = required_text
        self._modal_title = modal_title
        self._on_confirm = on_confirm

    async def callback(self, interaction: discord.Interaction) -> None:
        modal = TypeToConfirmModal(
            title=self._modal_title, required_text=self._required_text, on_confirm=self._on_confirm
        )
        await interaction.response.send_modal(modal)


class CancelDestructiveActionButton(discord.ui.Button):
    def __init__(self, *, custom_id: str, on_cancel: OnDestructiveCancelled) -> None:
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self._on_cancel = on_cancel

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_cancel(interaction)


class DestructiveConfirmationView(discord.ui.View):
    """The shared "button opens a type-to-confirm modal, or Cancel" prompt.

    Used unchanged by suggestion database reset, factory reset, and
    Replace-mode import -- only the label/required phrase/modal title
    differ between them.
    """

    def __init__(
        self,
        *,
        button_label: str,
        required_text: str,
        modal_title: str,
        custom_id_prefix: str,
        on_confirm: OnTypedConfirm,
        on_cancel: OnDestructiveCancelled,
    ) -> None:
        super().__init__(timeout=DESTRUCTIVE_CONFIRMATION_TIMEOUT_SECONDS)
        self.add_item(
            OpenTypeToConfirmButton(
                label=button_label,
                custom_id=f"wpm_{custom_id_prefix}_open",
                required_text=required_text,
                modal_title=modal_title,
                on_confirm=on_confirm,
            )
        )
        self.add_item(
            CancelDestructiveActionButton(custom_id=f"wpm_{custom_id_prefix}_cancel", on_cancel=on_cancel)
        )
