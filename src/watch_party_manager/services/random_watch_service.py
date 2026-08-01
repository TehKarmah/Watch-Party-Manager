"""The one focused piece of new logic /random_watch actually needs:
uniform random selection from an already-resolved pool.

Everything else /random_watch does -- collection resolution, eligibility,
member/genre filtering, option/label safety -- is orchestration of
existing services (CollectionEligibilityService,
services/nominee_pool_filter.py, services/discord_ui_limits.py); see
bot.py's random-watch handlers. This module exists only because nothing
else in the codebase performs a uniform, unweighted, single-item draw --
every existing selection path (NomineeSelectionService, the three
CandidateSelectionStrategy implementations) is deliberately about
choosing and weighting *multiple* nominees, not this.
"""

from __future__ import annotations

import random
from typing import Optional, Sequence

from watch_party_manager.domain.watch_item import WatchItem


def choose_random_watch_item(pool: Sequence[WatchItem]) -> Optional[WatchItem]:
    """Choose one item from `pool` with equal probability for every item.

    Deliberately not weighted by suggestion date, nomination history, or
    anything else -- every item in `pool` must have exactly the same
    chance of being chosen, unlike Favor New/Older Additions. Returns
    None for an empty pool rather than raising, since "no eligible items"
    is an expected, common outcome the caller already has its own clear
    messaging for.

    Args:
        pool: The already fully-resolved eligible (and, if applicable,
            filtered) pool to choose from. This function performs no
            eligibility or filtering of its own -- see
            CollectionEligibilityService/nominee_pool_filter.py for that.

    Returns:
        One uniformly-random item from `pool`, or None if `pool` is empty.
    """
    if not pool:
        return None
    return random.choice(list(pool))
