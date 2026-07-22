"""Tests for FR-030's Discord UI components (membership_view.py).

Mirrors test_config_view.py/test_setup_wizard_view.py's pattern:
constructing the view and confirming its buttons carry stable
custom_ids and forward clicks (with the clicked request_id) to the
supplied callback.
"""

import unittest

from watch_party_manager.membership_view import (
    MembershipApprovalView,
    build_membership_approve_button_custom_id,
    build_membership_deny_button_custom_id,
)


async def _noop(*args) -> None:
    pass


class MembershipApprovalViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_two_buttons_and_is_persistent(self) -> None:
        view = MembershipApprovalView(1, _noop, _noop)
        self.assertEqual(len(view.children), 2)
        self.assertIsNone(view.timeout)

    async def test_buttons_have_stable_custom_ids_encoding_the_request_id(self) -> None:
        view = MembershipApprovalView(42, _noop, _noop)
        self.assertEqual(view.children[0].custom_id, build_membership_approve_button_custom_id(42))
        self.assertEqual(view.children[1].custom_id, build_membership_deny_button_custom_id(42))

    async def test_labels_are_approve_and_deny(self) -> None:
        view = MembershipApprovalView(1, _noop, _noop)
        self.assertEqual(view.children[0].label, "Approve")
        self.assertEqual(view.children[1].label, "Deny")

    async def test_approve_button_forwards_the_request_id(self) -> None:
        calls = []

        async def on_approve(interaction, request_id) -> None:
            calls.append(request_id)

        view = MembershipApprovalView(7, on_approve, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, [7])

    async def test_deny_button_forwards_the_request_id(self) -> None:
        calls = []

        async def on_deny(interaction, request_id) -> None:
            calls.append(request_id)

        view = MembershipApprovalView(7, _noop, on_deny)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, [7])

    async def test_rejects_a_non_positive_request_id(self) -> None:
        with self.assertRaises(ValueError):
            MembershipApprovalView(0, _noop, _noop)

    async def test_custom_ids_differ_for_different_requests(self) -> None:
        self.assertNotEqual(
            build_membership_approve_button_custom_id(1), build_membership_approve_button_custom_id(2)
        )
        self.assertNotEqual(
            build_membership_approve_button_custom_id(1), build_membership_deny_button_custom_id(1)
        )


if __name__ == "__main__":
    unittest.main()
