"""Tests for FR-033A's generic Discord-safe pagination."""

from __future__ import annotations

import unittest

from watch_party_manager.pagination_view import PaginatedListView, paginate_lines


class FakeResponse:
    def __init__(self) -> None:
        self.edited_content = None
        self.edited_view = None
        self.sent_message = None
        self.sent_ephemeral = None

    async def edit_message(self, content=None, view=None) -> None:
        self.edited_content = content
        self.edited_view = view

    async def send_message(self, content, ephemeral=False) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeInteraction:
    def __init__(self, user_id: int = 1) -> None:
        self.user = FakeUser(user_id)
        self.response = FakeResponse()


class PaginateLinesTests(unittest.TestCase):
    def test_single_page_when_everything_fits(self) -> None:
        pages = paginate_lines("Header", ["one", "two", "three"])

        self.assertEqual(1, len(pages))
        self.assertIn("Header", pages[0])
        self.assertIn("one", pages[0])
        self.assertNotIn("Page 1 of", pages[0])

    def test_no_permanent_item_cap(self) -> None:
        lines = [f"item {i}" for i in range(500)]

        pages = paginate_lines("Header", lines, max_page_length=500)

        total_items = sum(page.count("item ") for page in pages)
        self.assertEqual(500, total_items)

    def test_every_page_stays_under_the_limit(self) -> None:
        lines = [f"A reasonably long suggestion line number {i} with some padding text" for i in range(200)]

        pages = paginate_lines("Header", lines, max_page_length=500)

        for page in pages:
            self.assertLessEqual(len(page), 500)

    def test_deterministic_ordering_across_pages(self) -> None:
        lines = [f"item {i}" for i in range(50)]

        pages = paginate_lines("Header", lines, max_page_length=200)

        rebuilt_order = []
        for page in pages:
            for line in lines:
                if line in page and line not in rebuilt_order:
                    rebuilt_order.append(line)
        self.assertEqual(lines, rebuilt_order)

    def test_multi_page_footer_shows_page_numbers(self) -> None:
        lines = [f"item {i}" for i in range(100)]

        pages = paginate_lines("Header", lines, max_page_length=200)

        self.assertGreater(len(pages), 1)
        self.assertIn(f"Page 1 of {len(pages)}", pages[0])
        self.assertIn(f"Page {len(pages)} of {len(pages)}", pages[-1])

    def test_empty_lines_returns_header_only_page(self) -> None:
        pages = paginate_lines("Header", [])

        self.assertEqual(["Header"], pages)


class PaginatedListViewTests(unittest.IsolatedAsyncioTestCase):
    def test_rejects_construction_with_no_pages(self) -> None:
        with self.assertRaises(ValueError):
            PaginatedListView([])

    def test_starts_on_the_first_page(self) -> None:
        view = PaginatedListView(["page 1", "page 2", "page 3"])

        self.assertEqual("page 1", view.current_page)
        self.assertEqual(0, view.current_index)

    def test_previous_disabled_on_first_page(self) -> None:
        view = PaginatedListView(["page 1", "page 2"])

        self.assertTrue(view.children[0].disabled)
        self.assertFalse(view.children[1].disabled)

    def test_next_disabled_on_single_page(self) -> None:
        view = PaginatedListView(["only page"])

        self.assertTrue(view.children[0].disabled)
        self.assertTrue(view.children[1].disabled)

    async def test_next_advances_the_page(self) -> None:
        view = PaginatedListView(["page 1", "page 2", "page 3"])
        interaction = FakeInteraction()

        await view.children[1].callback(interaction)

        self.assertEqual("page 2", view.current_page)
        self.assertEqual("page 2", interaction.response.edited_content)

    async def test_previous_goes_back(self) -> None:
        view = PaginatedListView(["page 1", "page 2", "page 3"])
        view._index = 1
        interaction = FakeInteraction()

        await view.children[0].callback(interaction)

        self.assertEqual("page 1", view.current_page)

    async def test_next_disabled_after_reaching_the_last_page(self) -> None:
        view = PaginatedListView(["page 1", "page 2"])
        interaction = FakeInteraction()

        await view.children[1].callback(interaction)

        self.assertTrue(view.children[1].disabled)

    async def test_next_is_a_no_op_past_the_last_page(self) -> None:
        view = PaginatedListView(["page 1", "page 2"])
        view._index = 1
        interaction = FakeInteraction()

        await view.children[1].callback(interaction)

        self.assertEqual("page 2", view.current_page)

    async def test_only_the_requester_can_page(self) -> None:
        view = PaginatedListView(["page 1", "page 2"], requester_id=42)
        interaction = FakeInteraction(user_id=99)

        allowed = await view.interaction_check(interaction)

        self.assertFalse(allowed)
        self.assertIn("Only the person", interaction.response.sent_message)

    async def test_requester_is_allowed(self) -> None:
        view = PaginatedListView(["page 1", "page 2"], requester_id=42)
        interaction = FakeInteraction(user_id=42)

        allowed = await view.interaction_check(interaction)

        self.assertTrue(allowed)

    async def test_no_requester_restriction_when_unset(self) -> None:
        view = PaginatedListView(["page 1", "page 2"])
        interaction = FakeInteraction(user_id=99)

        allowed = await view.interaction_check(interaction)

        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
