"""Shared Discord UI for editing the five nominee-pool filters -- Genre,
IMDb Rating, MPAA Rating, Actor, and Member -- used identically by both
/vote start's Custom Vote Filters and /random watch's Add Filters, so
warning wording, filter summaries, validation behavior, and button
enabling/disabling can never diverge between the two flows.

Discord hard-caps one message at 5 action rows, and a Select/UserSelect
each occupies a full row by itself -- five distinct filter controls plus
a primary action button (and, for /random watch, Change Collection)
cannot all fit on one screen at once. FilterMenuView is the answer: one
row picks which filter to edit next (always offered in the fixed
Genre/IMDb Rating/MPAA Rating/Actor/Member order, Member always last),
and editing happens on that filter's own small, focused screen (or
directly in a modal for the two free-text filters -- IMDb Rating and
Actor), always returning to a refreshed FilterMenuView afterward. This
also means a future filter -- or an entirely new consumer, like the
planned /browse command -- is just one more category option and one
more small edit screen, never a redesign of this module's shape.

Every editor screen follows the same standardized layout: the filter's
own picker (a Select/UserSelect, or a button that opens a modal for the
two free-text filters), a single ClearFilterButton labeled "Clear
Filter" (replacing what used to be five differently-worded resets --
"Any Genre"/"Any IMDb Rating"/"Any MPAA Rating"/"Any Actor"/"Any
Member"), then Back. Clearing always resets only the filter currently
being edited and returns immediately to a refreshed FilterMenuView,
whose category select and the shared Current Filters summary
(bot.py's build_current_filters_summary) both update to show "Any ..."
for that filter right away.

Like start_vote_view.py/random_watch_view.py, this module has no
dependency on bot.py: every view/component here only knows how to
render itself and forward a click/selection/submission to a
caller-supplied callback. All filtering, validation, and session-state
logic lives in bot.py's handlers and the services they orchestrate
(services/nominee_pool_filter.py, services/member_filter_validation.py).
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Optional

import discord

FILTER_MENU_VIEW_TIMEOUT_SECONDS = 180

OnFilterAction = Callable[[discord.Interaction], Awaitable[None]]
OnCategorySelected = Callable[[discord.Interaction, str], Awaitable[None]]
OnGenreChanged = Callable[[discord.Interaction, Optional[str]], Awaitable[None]]
OnMpaaRatingChanged = Callable[[discord.Interaction, Optional[str]], Awaitable[None]]
OnMemberChanged = Callable[[discord.Interaction, Optional[discord.Member]], Awaitable[None]]
OnImdbRatingSubmitted = Callable[[discord.Interaction, Optional[str], Optional[str]], Awaitable[None]]
OnActorSearchSubmitted = Callable[[discord.Interaction, Optional[str]], Awaitable[None]]
OnActorMatchSelected = Callable[[discord.Interaction, str], Awaitable[None]]

FILTER_CATEGORY_GENRE = "genre"
FILTER_CATEGORY_IMDB_RATING = "imdb_rating"
FILTER_CATEGORY_MPAA_RATING = "mpaa_rating"
FILTER_CATEGORY_ACTOR = "actor"
FILTER_CATEGORY_MEMBER = "member"

# The one fixed display order every caller must use (Shared Filter
# Order): Genre, IMDb Rating, MPAA Rating, Actor, Member -- Member last.
FILTER_CATEGORY_ORDER = (
    FILTER_CATEGORY_GENRE,
    FILTER_CATEGORY_IMDB_RATING,
    FILTER_CATEGORY_MPAA_RATING,
    FILTER_CATEGORY_ACTOR,
    FILTER_CATEGORY_MEMBER,
)

FILTER_CATEGORY_LABELS = {
    FILTER_CATEGORY_GENRE: "Genre",
    FILTER_CATEGORY_IMDB_RATING: "IMDb Rating",
    FILTER_CATEGORY_MPAA_RATING: "MPAA Rating",
    FILTER_CATEGORY_ACTOR: "Actor",
    FILTER_CATEGORY_MEMBER: "Member",
}

# How much breathing room the dot leader gets past the longest category
# label ("IMDb Rating"/"MPAA Rating", both 11 characters) -- chosen so a
# short label like "Genre" still gets a visually substantial leader
# rather than just one or two dots.
_CURRENT_FILTERS_LABEL_PADDING = 5


def format_current_filters_block(current_values: dict, *, header: str = "Current Filters") -> str:
    """Render a Discord-friendly, dot-leader-aligned summary of each
    filter category's current value, always in FILTER_CATEGORY_ORDER --
    e.g.:

        **Current Filters**

        Genre .......... Sci-Fi
        IMDb Rating .... 7.0+
        MPAA Rating .... Any MPAA Rating
        Actor .......... Any Actor
        Member ......... KC

    `current_values` maps each FILTER_CATEGORY_* constant to its current
    display value (an inactive filter's own "Any ..." wording, never
    omitted) -- see bot.py's build_filter_category_current_values, the
    one place that dict is actually built from a filter_state. This
    function only knows how to lay values out, not where they come
    from, so it's reusable as-is by any future consumer of this shared
    filter menu (e.g. the planned /browse command) without any changes
    here.
    """
    label_width = max(len(FILTER_CATEGORY_LABELS[category]) for category in FILTER_CATEGORY_ORDER)
    leader_width = label_width + _CURRENT_FILTERS_LABEL_PADDING
    lines = [f"**{header}**", ""]
    for category in FILTER_CATEGORY_ORDER:
        label = FILTER_CATEGORY_LABELS[category]
        value = current_values.get(category) or "Any"
        dots = "." * max(leader_width - len(label) - 1, 3)
        lines.append(f"{label} {dots} {value}")
    return "\n".join(lines)


class FilterCategorySelectComponent(discord.ui.Select):
    """Picks which filter to edit next -- the one row every FilterMenuView
    needs regardless of how many filters exist, always offered in the
    fixed Genre/IMDb Rating/MPAA Rating/Actor/Member order.
    """

    def __init__(
        self,
        on_selected: OnCategorySelected,
        *,
        current_values: dict,
        custom_id: str = "wpm_filter_menu_category",
    ) -> None:
        options = [
            discord.SelectOption(
                label=f"{FILTER_CATEGORY_LABELS[category]}: {current_values.get(category) or 'Any'}"[:100],
                value=category,
            )
            for category in FILTER_CATEGORY_ORDER
        ]
        super().__init__(placeholder="Choose a filter to edit...", options=options, custom_id=custom_id)
        self._on_selected = on_selected

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_selected(interaction, self.values[0])


class FilterMenuActionButton(discord.ui.Button):
    def __init__(
        self,
        on_click: OnFilterAction,
        *,
        label: str,
        custom_id: str,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> None:
        super().__init__(label=label, style=style, custom_id=custom_id, disabled=disabled)
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ClearFilterButton(discord.ui.Button):
    """The one, consistently-worded reset action every filter editor
    offers -- "Clear Filter", always styled and labeled identically,
    replacing what used to be five differently-worded reset affordances
    ("Any Genre"/"Any IMDb Rating"/"Any MPAA Rating"/"Any Actor"/
    "Any Member") across the five editors. Clears only the filter
    currently being edited and returns immediately to the refreshed
    FilterMenuView -- on_click is each editor's own on_change callback
    invoked with None, so clearing here behaves identically to a native
    Select "clear selection" gesture where one is also available.
    """

    def __init__(self, on_click: OnFilterAction, *, custom_id: str) -> None:
        super().__init__(label="Clear Filter", style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class FilterMenuView(discord.ui.View):
    """The filter-editing entry point for both Custom Vote and
    /random watch: pick a filter to edit, or continue. Exactly 2-3 rows
    (category select + primary action, plus an optional secondary
    action like /random watch's Change Collection), leaving comfortable
    headroom under Discord's 5-row limit for future growth.
    """

    def __init__(
        self,
        on_category_selected: OnCategorySelected,
        on_primary_action: OnFilterAction,
        *,
        current_values: dict,
        primary_action_label: str,
        primary_action_custom_id: str,
        primary_action_disabled: bool = False,
        on_secondary_action: Optional[OnFilterAction] = None,
        secondary_action_label: Optional[str] = None,
        secondary_action_custom_id: str = "wpm_filter_menu_secondary",
    ) -> None:
        super().__init__(timeout=FILTER_MENU_VIEW_TIMEOUT_SECONDS)
        self.add_item(FilterCategorySelectComponent(on_category_selected, current_values=current_values))
        self.primary_button = FilterMenuActionButton(
            on_primary_action,
            label=primary_action_label,
            custom_id=primary_action_custom_id,
            style=discord.ButtonStyle.primary,
            disabled=primary_action_disabled,
        )
        self.add_item(self.primary_button)
        if on_secondary_action is not None:
            self.add_item(
                FilterMenuActionButton(
                    on_secondary_action,
                    label=secondary_action_label or "Back",
                    custom_id=secondary_action_custom_id,
                )
            )


class BackToFilterMenuButton(discord.ui.Button):
    def __init__(self, on_click: OnFilterAction, *, custom_id: str = "wpm_filter_menu_back") -> None:
        super().__init__(label="Back to Filters", style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class GenreEditSelectComponent(discord.ui.Select):
    """min_values=0 so Discord's own "clear this selection" affordance
    also works, but every editor's primary, always-visible reset path is
    now the standalone Clear Filter button on GenreEditView below.
    """

    def __init__(
        self, on_change: OnGenreChanged, *, options: List[discord.SelectOption], custom_id: str = "wpm_filter_menu_genre"
    ) -> None:
        super().__init__(placeholder="Change Genre...", min_values=0, max_values=1, options=options, custom_id=custom_id)
        self._on_change = on_change

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_change(interaction, self.values[0] if self.values else None)


class GenreEditView(discord.ui.View):
    """The standardized editor shape every filter now shares: the
    filter's own picker control, a Clear Filter button, then Back.
    """

    def __init__(
        self,
        on_change: OnGenreChanged,
        on_back: OnFilterAction,
        *,
        options: List[discord.SelectOption],
        clear_custom_id: str = "wpm_filter_menu_genre_clear",
    ) -> None:
        super().__init__(timeout=FILTER_MENU_VIEW_TIMEOUT_SECONDS)
        self.add_item(GenreEditSelectComponent(on_change, options=options))
        self.add_item(ClearFilterButton(lambda interaction: on_change(interaction, None), custom_id=clear_custom_id))
        self.add_item(BackToFilterMenuButton(on_back))


class MpaaRatingEditSelectComponent(discord.ui.Select):
    """min_values=0, matching Genre/Member's own select shape -- MPAA
    Rating no longer carries its own inline "Any MPAA Rating" option;
    resetting it now goes through the same standalone Clear Filter
    button every other editor uses (see MpaaRatingEditView below).
    """

    def __init__(
        self,
        on_change: OnMpaaRatingChanged,
        *,
        options: List[discord.SelectOption],
        custom_id: str = "wpm_filter_menu_mpaa_rating",
    ) -> None:
        super().__init__(
            placeholder="Choose MPAA Rating...", min_values=0, max_values=1, options=options, custom_id=custom_id
        )
        self._on_change = on_change

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_change(interaction, self.values[0] if self.values else None)


class MpaaRatingEditView(discord.ui.View):
    def __init__(
        self,
        on_change: OnMpaaRatingChanged,
        on_back: OnFilterAction,
        *,
        options: List[discord.SelectOption],
        clear_custom_id: str = "wpm_filter_menu_mpaa_rating_clear",
    ) -> None:
        super().__init__(timeout=FILTER_MENU_VIEW_TIMEOUT_SECONDS)
        self.add_item(MpaaRatingEditSelectComponent(on_change, options=options))
        self.add_item(ClearFilterButton(lambda interaction: on_change(interaction, None), custom_id=clear_custom_id))
        self.add_item(BackToFilterMenuButton(on_back))


class MemberEditSelectComponent(discord.ui.UserSelect):
    """A discord.ui.UserSelect, not a plain Select, so it never fails on
    servers with more than 25 members -- Discord resolves member options
    dynamically rather than needing a fixed option list. min_values=0 so
    Discord's own "clear this selection" affordance also works, on top
    of MemberEditView's own standalone Clear Filter button.
    """

    def __init__(self, on_change: OnMemberChanged, *, custom_id: str = "wpm_filter_menu_member") -> None:
        super().__init__(placeholder="Choose Member...", min_values=0, max_values=1, custom_id=custom_id)
        self._on_change = on_change

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_change(interaction, self.values[0] if self.values else None)


class MemberEditView(discord.ui.View):
    def __init__(
        self,
        on_change: OnMemberChanged,
        on_back: OnFilterAction,
        *,
        clear_custom_id: str = "wpm_filter_menu_member_clear",
    ) -> None:
        super().__init__(timeout=FILTER_MENU_VIEW_TIMEOUT_SECONDS)
        self.add_item(MemberEditSelectComponent(on_change))
        self.add_item(ClearFilterButton(lambda interaction: on_change(interaction, None), custom_id=clear_custom_id))
        self.add_item(BackToFilterMenuButton(on_back))


class ImdbRatingEditView(discord.ui.View):
    """A modal collects the actual minimum/maximum (Discord selects
    can't do free-text numeric ranges); Clear Filter is its own
    explicit, separately clickable reset action rather than only a
    blank-modal-submission convention -- the same standalone-button
    shape every other editor uses.
    """

    def __init__(
        self,
        on_set: OnFilterAction,
        on_clear: OnFilterAction,
        on_back: OnFilterAction,
        *,
        clear_custom_id: str = "wpm_filter_menu_imdb_rating_clear",
    ) -> None:
        super().__init__(timeout=FILTER_MENU_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            FilterMenuActionButton(on_set, label="Set Rating Range", custom_id="wpm_filter_menu_imdb_rating_set")
        )
        self.add_item(ClearFilterButton(on_clear, custom_id=clear_custom_id))
        self.add_item(BackToFilterMenuButton(on_back))


class ImdbRatingModal(discord.ui.Modal):
    def __init__(
        self,
        on_submit: OnImdbRatingSubmitted,
        *,
        default_minimum: str = "",
        default_maximum: str = "",
    ) -> None:
        super().__init__(title="Set IMDb Rating")
        self._submit_callback = on_submit
        self.minimum_input = discord.ui.TextInput(
            label="Minimum IMDb Rating (0.0-10.0)",
            required=False,
            default=default_minimum or None,
            placeholder="e.g. 7.0 -- leave blank for no minimum",
        )
        self.maximum_input = discord.ui.TextInput(
            label="Maximum IMDb Rating (0.0-10.0)",
            required=False,
            default=default_maximum or None,
            placeholder="e.g. 8.5 -- leave blank for no maximum",
        )
        self.add_item(self.minimum_input)
        self.add_item(self.maximum_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.minimum_input.value or None, self.maximum_input.value or None)


class ActorEditView(discord.ui.View):
    """A modal collects the free-text actor search (Discord selects
    can't do free-text entry); Clear Filter is its own explicit,
    separately clickable reset action, matching ImdbRatingEditView's
    same standalone-button shape.
    """

    def __init__(
        self,
        on_search: OnFilterAction,
        on_clear: OnFilterAction,
        on_back: OnFilterAction,
        *,
        clear_custom_id: str = "wpm_filter_menu_actor_clear",
    ) -> None:
        super().__init__(timeout=FILTER_MENU_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            FilterMenuActionButton(on_search, label="Search Actor", custom_id="wpm_filter_menu_actor_search")
        )
        self.add_item(ClearFilterButton(on_clear, custom_id=clear_custom_id))
        self.add_item(BackToFilterMenuButton(on_back))


class ActorSearchModal(discord.ui.Modal):
    def __init__(self, on_submit: OnActorSearchSubmitted) -> None:
        super().__init__(title="Search Actor")
        self._submit_callback = on_submit
        self.query_input = discord.ui.TextInput(
            label="Actor name (part or all)",
            required=False,
            placeholder="e.g. Jim Carrey -- leave blank for Any Actor",
        )
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.query_input.value or None)


class ActorMatchSelectComponent(discord.ui.Select):
    """Shown only when an actor search matched more than one stored cast
    name -- one option per matching actor, already built by the caller
    through build_safe_select_option/cap_select_options (see
    bot.py's build_actor_match_select_options) so a long name or a
    search matching more than 25 actors can never produce an invalid
    payload.
    """

    def __init__(
        self,
        on_selected: OnActorMatchSelected,
        *,
        options: List[discord.SelectOption],
        custom_id: str = "wpm_filter_menu_actor_match",
    ) -> None:
        super().__init__(placeholder="Choose an actor...", min_values=1, max_values=1, options=options, custom_id=custom_id)
        self._on_selected = on_selected

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_selected(interaction, self.values[0])


class ActorMatchEditView(discord.ui.View):
    def __init__(
        self, on_selected: OnActorMatchSelected, on_back: OnFilterAction, *, options: List[discord.SelectOption]
    ) -> None:
        super().__init__(timeout=FILTER_MENU_VIEW_TIMEOUT_SECONDS)
        self.add_item(ActorMatchSelectComponent(on_selected, options=options))
        self.add_item(BackToFilterMenuButton(on_back))


__all__ = [
    "FILTER_MENU_VIEW_TIMEOUT_SECONDS",
    "FILTER_CATEGORY_GENRE",
    "FILTER_CATEGORY_IMDB_RATING",
    "FILTER_CATEGORY_MPAA_RATING",
    "FILTER_CATEGORY_ACTOR",
    "FILTER_CATEGORY_MEMBER",
    "FILTER_CATEGORY_ORDER",
    "FILTER_CATEGORY_LABELS",
    "format_current_filters_block",
    "FilterCategorySelectComponent",
    "FilterMenuView",
    "BackToFilterMenuButton",
    "ClearFilterButton",
    "GenreEditSelectComponent",
    "GenreEditView",
    "MpaaRatingEditSelectComponent",
    "MpaaRatingEditView",
    "MemberEditSelectComponent",
    "MemberEditView",
    "ImdbRatingEditView",
    "ImdbRatingModal",
    "ActorEditView",
    "ActorSearchModal",
    "ActorMatchSelectComponent",
    "ActorMatchEditView",
]
