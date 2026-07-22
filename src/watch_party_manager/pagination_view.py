"""Generic Discord-safe pagination for long text lists (FR-033A).

No pagination component existed anywhere in this project before this
milestone (confirmed by inspecting the whole repository for
"paginat*"). Everywhere else either fits in one message or applies a
hard truncation cap. This module is deliberately domain-agnostic --
it operates on pre-rendered page strings, so any future long-list
command can reuse it, not just /list.
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Optional, Sequence

import discord

PAGINATION_VIEW_TIMEOUT_SECONDS = 180
DEFAULT_MAX_PAGE_LENGTH = 1900
PAGE_FOOTER_RESERVE = 30

OnPaginationButtonClick = Callable[[discord.Interaction], Awaitable[None]]


def paginate_lines(header: str, lines: Sequence[str], *, max_page_length: int = DEFAULT_MAX_PAGE_LENGTH) -> List[str]:
    """Split rendered lines into Discord-safe pages, each starting with header.

    Deterministic ordering: lines are placed in the given order and
    filled greedily into as few pages as fit under max_page_length --
    never a fixed item-count cap, so a page holds however many lines
    actually fit. A "Page X of Y" footer is appended whenever there is
    more than one page; space for it is reserved up front so adding it
    never pushes a page over max_page_length.

    Args:
        header: A heading repeated at the top of every page.
        lines: Pre-rendered lines, one per item, in the exact order
            they should appear.
        max_page_length: The hard ceiling for each page's total length.
            Defaults comfortably under Discord's 2000-character message
            limit.
    """
    if not lines:
        return [header]

    effective_max = max_page_length - PAGE_FOOTER_RESERVE
    pages: List[List[str]] = []
    current: List[str] = []
    current_length = len(header) + 2

    for line in lines:
        line_length = len(line) + 1
        if current and current_length + line_length > effective_max:
            pages.append(current)
            current = []
            current_length = len(header) + 2
        current.append(line)
        current_length += line_length
    pages.append(current)

    total = len(pages)
    rendered: List[str] = []
    for index, page_lines in enumerate(pages):
        body = "\n".join([header, "", *page_lines])
        if total > 1:
            body += f"\n\nPage {index + 1} of {total}"
        rendered.append(body)
    return rendered


class PaginationButton(discord.ui.Button):
    def __init__(self, label: str, on_click: OnPaginationButtonClick) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class PaginatedListView(discord.ui.View):
    """A Previous/Next-button view over a fixed sequence of pre-rendered pages.

    Each page is displayed exactly as given -- the caller (see
    paginate_lines()) is responsible for keeping every page under
    Discord's message-length limit. This view only tracks which page is
    current and disables Previous/Next at the boundaries.
    """

    def __init__(self, pages: Sequence[str], *, requester_id: Optional[int] = None) -> None:
        if not pages:
            raise ValueError("pages must contain at least one page")
        super().__init__(timeout=PAGINATION_VIEW_TIMEOUT_SECONDS)
        self._pages = list(pages)
        self._index = 0
        self._requester_id = requester_id
        self._previous_button = PaginationButton("Previous", self._go_previous)
        self._next_button = PaginationButton("Next", self._go_next)
        self.add_item(self._previous_button)
        self.add_item(self._next_button)
        self._sync_button_state()

    @property
    def current_page(self) -> str:
        return self._pages[self._index]

    @property
    def page_count(self) -> int:
        return len(self._pages)

    @property
    def current_index(self) -> int:
        return self._index

    def _sync_button_state(self) -> None:
        self._previous_button.disabled = self._index == 0
        self._next_button.disabled = self._index >= len(self._pages) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Restrict paging to whoever ran the command, when known.

        Every /list response is ephemeral-or-crew-only already, so in
        practice only the requester can normally see this view at all;
        this is defense in depth, matching this project's established
        pattern of re-checking rather than trusting visibility alone.
        """
        if self._requester_id is not None and interaction.user.id != self._requester_id:
            await interaction.response.send_message(
                "Only the person who ran this command can page through it.", ephemeral=True
            )
            return False
        return True

    async def _go_previous(self, interaction: discord.Interaction) -> None:
        if self._index > 0:
            self._index -= 1
        await self._refresh(interaction)

    async def _go_next(self, interaction: discord.Interaction) -> None:
        if self._index < len(self._pages) - 1:
            self._index += 1
        await self._refresh(interaction)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._sync_button_state()
        await interaction.response.edit_message(content=self.current_page, view=self)
