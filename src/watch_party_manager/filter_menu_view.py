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
also means a future filter is just one more category option and one
more small edit screen, never a redesign of this module's shape.

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

ANY_MPAA_RATING_VALUE = "__any_mpaa_rating__"


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
    """min_values=0 so Discord's own "clear this selection" affordance is
    how a member returns to Any Genre -- no separate button is needed.
    """

    def __init__(
        self, on_change: OnGenreChanged, *, options: List[discord.SelectOption], custom_id: str = "wpm_filter_menu_genre"
    ) -> None:
        super().__init__(
            placeholder="Genre: Any Genre (optional)", min_values=0, max_values=1, options=options, custom_id=custom_id
        )
        self._on_change = on_change

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_change(interaction, self.values[0] if self.values else None)


class GenreEditView(discord.ui.View):
    def __init__(
        self, on_change: OnGenreChanged, on_back: OnFilterAction, *, options: List[discord.SelectOption]
    ) -> None:
        super().__init__(timeout=FILTER_MENU_VIEW_TIMEOUT_SECONDS)
        self.add_item(GenreEditSelectComponent(on_change, options=options))
        self.add_item(BackToFilterMenuButton(on_back))


class MpaaRatingEditSelectComponent(discord.ui.Select):
    """Section 7: Any MPAA Rating is always the first, explicit option --
    unlike Genre/Member, this select always has exactly one value
    selected (min_values=1); "Any" is a real option, not a native-clear
    affordance.
    """

    def __init__(
        self,
        on_change: OnMpaaRatingChanged,
        *,
        options: List[discord.SelectOption],
        custom_id: str = "wpm_filter_menu_mpaa_rating",
    ) -> None:
        all_options = [discord.SelectOption(label="Any MPAA Rating", value=ANY_MPAA_RATING_VALUE)] + list(options)
        super().__init__(
            placeholder="Choose an MPAA rating...", min_values=1, max_values=1, options=all_options, custom_id=custom_id
        )
        self._on_change = on_change

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        await self._on_change(interaction, None if selected == ANY_MPAA_RATING_VALUE else selected)


class MpaaRatingEditView(discord.ui.View):
    def __init__(
        self, on_change: OnMpaaRatingChanged, on_back: OnFilterAction, *, options: List[discord.SelectOption]
    ) -> None:
        super().__init__(timeout=FILTER_MENU_VIEW_TIMEOUT_SECONDS)
        self.add_item(MpaaRatingEditSelectComponent(on_change, options=options))
        self.add_item(BackToFilterMenuButton(on_back))


class MemberEditSelectComponent(discord.ui.UserSelect):
    """A discord.ui.UserSelect, not a plain Select, so it never fails on
    servers with more than 25 members -- Discord resolves member options
    dynamically rather than needing a fixed option list. min_values=0 so
    Discord's own "clear this selection" affordance is how a member
    returns to Any Member.
    """

    def __init__(self, on_change: OnMemberChanged, *, custom_id: str = "wpm_filter_menu_member") -> None:
        super().__init__(
            placeholder="Suggestion Source: Any Member (optional)", min_values=0, max_values=1, custom_id=custom_id
        )
        self._on_change = on_change

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_change(interaction, self.values[0] if self.values else None)


class MemberEditView(discord.ui.View):
    def __init__(self, on_change: OnMemberChanged, on_back: OnFilterAction) -> None:
        super().__init__(timeout=FILTER_MENU_VIEW_TIMEOUT_SECONDS)
        self.add_item(MemberEditSelectComponent(on_change))
        self.add_item(BackToFilterMenuButton(on_back))


class ImdbRatingEditView(discord.ui.View):
    """Section 6: a modal collects the actual minimum/maximum (Discord
    selects can't do free-text numeric ranges); "Any IMDb Rating" is its
    own explicit, separately clickable reset action rather than only a
    blank-modal-submission convention.
    """

    def __init__(self, on_set: OnFilterAction, on_any: OnFilterAction, on_back: OnFilterAction) -> None:
        super().__init__(timeout=FILTER_MENU_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            FilterMenuActionButton(
                on_set, label="Set Minimum/Maximum...", custom_id="wpm_filter_menu_imdb_rating_set"
            )
        )
        self.add_item(
            FilterMenuActionButton(on_any, label="Any IMDb Rating", custom_id="wpm_filter_menu_imdb_rating_any")
        )
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
            label="Minimum rating (0.0-10.0)",
            required=False,
            default=default_minimum or None,
            placeholder="e.g. 7.0 -- leave blank for no minimum",
        )
        self.maximum_input = discord.ui.TextInput(
            label="Maximum rating (0.0-10.0)",
            required=False,
            default=default_maximum or None,
            placeholder="e.g. 8.5 -- leave blank for no maximum",
        )
        self.add_item(self.minimum_input)
        self.add_item(self.maximum_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.minimum_input.value or None, self.maximum_input.value or None)


class ActorEditView(discord.ui.View):
    """Section 8: a modal collects the free-text actor search (Discord
    selects can't do free-text entry); "Any Actor" is its own explicit,
    separately clickable reset action, matching ImdbRatingEditView's
    same shape.
    """

    def __init__(self, on_search: OnFilterAction, on_any: OnFilterAction, on_back: OnFilterAction) -> None:
        super().__init__(timeout=FILTER_MENU_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            FilterMenuActionButton(on_search, label="Search for an Actor...", custom_id="wpm_filter_menu_actor_search")
        )
        self.add_item(FilterMenuActionButton(on_any, label="Any Actor", custom_id="wpm_filter_menu_actor_any"))
        self.add_item(BackToFilterMenuButton(on_back))


class ActorSearchModal(discord.ui.Modal):
    def __init__(self, on_submit: OnActorSearchSubmitted) -> None:
        super().__init__(title="Search for an Actor")
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
    "ANY_MPAA_RATING_VALUE",
    "FilterCategorySelectComponent",
    "FilterMenuView",
    "BackToFilterMenuButton",
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
