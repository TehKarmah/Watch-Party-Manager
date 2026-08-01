"""Tests for services/random_watch_service.py: the one genuinely new
piece of logic /random_watch needs -- a uniform, unweighted draw from an
already-resolved pool. Collection resolution, eligibility, and filtering
are all covered by their own existing test suites (this module performs
none of that itself).
"""

import unittest
from collections import Counter
from unittest.mock import patch

from watch_party_manager.domain.watch_item import MediaType, WatchItem
from watch_party_manager.services.random_watch_service import choose_random_watch_item


def _item(item_id: int, title: str = "Movie") -> WatchItem:
    return WatchItem(title=f"{title} {item_id}", media_type=MediaType.MOVIE, id=item_id)


class ChooseRandomWatchItemTests(unittest.TestCase):
    def test_returns_none_for_an_empty_pool(self) -> None:
        self.assertIsNone(choose_random_watch_item([]))

    def test_returns_the_only_item_in_a_single_item_pool(self) -> None:
        item = _item(1)
        self.assertIs(choose_random_watch_item([item]), item)

    def test_returned_item_always_comes_from_the_pool(self) -> None:
        pool = [_item(i) for i in range(1, 6)]
        for _ in range(50):
            choice = choose_random_watch_item(pool)
            self.assertIn(choice, pool)

    def test_delegates_to_random_choice_uniformly(self) -> None:
        # Confirms the selection path is exactly random.choice() over the
        # supplied pool -- no weighting, no sorting, no filtering applied
        # inside this function.
        pool = [_item(i) for i in range(1, 4)]
        with patch("watch_party_manager.services.random_watch_service.random.choice") as mock_choice:
            mock_choice.return_value = pool[1]
            result = choose_random_watch_item(pool)
        mock_choice.assert_called_once_with(pool)
        self.assertIs(result, pool[1])

    def test_every_item_can_be_selected_over_many_draws(self) -> None:
        # Not a statistical proof of uniformity, just a sanity check that
        # nothing structurally excludes an item (e.g. always picking the
        # first or last element).
        pool = [_item(i) for i in range(1, 6)]
        counts = Counter()
        for _ in range(500):
            counts[choose_random_watch_item(pool).id] += 1
        self.assertEqual(set(counts.keys()), {item.id for item in pool})

    def test_accepts_a_tuple_pool_not_just_a_list(self) -> None:
        pool = (_item(1), _item(2))
        self.assertIn(choose_random_watch_item(pool), pool)

    def test_does_not_mutate_the_supplied_pool(self) -> None:
        pool = [_item(1), _item(2), _item(3)]
        original = list(pool)
        choose_random_watch_item(pool)
        self.assertEqual(pool, original)


if __name__ == "__main__":
    unittest.main()
