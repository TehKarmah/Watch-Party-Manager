"""Tests for the shared role/channel validation helpers (FR-029) reused by
both FR-028's /setup wizard and FR-029's /config command.
"""

import unittest

from watch_party_manager.services.configuration_validation import (
    validate_channel_usable,
    validate_role_exists,
)


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakePermissions:
    def __init__(self, view_channel: bool = True, send_messages: bool = True) -> None:
        self.view_channel = view_channel
        self.send_messages = send_messages


class FakeChannel:
    def __init__(self, channel_id: int, *, permissions: FakePermissions = None) -> None:
        self.id = channel_id
        self._permissions = permissions or FakePermissions()

    def permissions_for(self, member) -> FakePermissions:
        return self._permissions


class FakeGuild:
    def __init__(self, *, role_ids=(), channels=None) -> None:
        self._role_ids = set(role_ids)
        self._channels = channels or {}
        self.me = object()

    def get_role(self, role_id):
        return FakeRole(role_id) if role_id in self._role_ids else None

    def get_channel_or_thread(self, channel_id):
        return self._channels.get(channel_id)


class ValidateRoleExistsTests(unittest.TestCase):
    def test_none_is_never_an_error(self) -> None:
        self.assertIsNone(validate_role_exists(None, FakeGuild()))

    def test_existing_role_is_valid(self) -> None:
        guild = FakeGuild(role_ids={111})
        self.assertIsNone(validate_role_exists(111, guild))

    def test_missing_role_returns_an_error_message(self) -> None:
        guild = FakeGuild(role_ids=set())
        error = validate_role_exists(111, guild)
        self.assertIsNotNone(error)
        self.assertIn("no longer exists", error)

    def test_resource_label_is_used_in_the_message(self) -> None:
        guild = FakeGuild(role_ids=set())
        error = validate_role_exists(111, guild, resource_label="WASH Crew role")
        self.assertIn("WASH Crew role", error)


class ValidateChannelUsableTests(unittest.TestCase):
    def test_none_is_never_an_error(self) -> None:
        self.assertIsNone(validate_channel_usable(None, FakeGuild()))

    def test_existing_usable_channel_is_valid(self) -> None:
        guild = FakeGuild(channels={400: FakeChannel(400)})
        self.assertIsNone(validate_channel_usable(400, guild))

    def test_missing_channel_returns_an_error_message(self) -> None:
        guild = FakeGuild(channels={})
        error = validate_channel_usable(400, guild)
        self.assertIsNotNone(error)
        self.assertIn("no longer exists", error)

    def test_insufficient_permissions_returns_an_error_message(self) -> None:
        guild = FakeGuild(
            channels={400: FakeChannel(400, permissions=FakePermissions(send_messages=False))}
        )
        error = validate_channel_usable(400, guild)
        self.assertIsNotNone(error)
        self.assertIn("permission", error)

    def test_missing_view_permission_is_also_rejected(self) -> None:
        guild = FakeGuild(
            channels={400: FakeChannel(400, permissions=FakePermissions(view_channel=False))}
        )
        error = validate_channel_usable(400, guild)
        self.assertIsNotNone(error)

    def test_resource_label_is_used_in_the_message(self) -> None:
        guild = FakeGuild(channels={})
        error = validate_channel_usable(400, guild, resource_label="watched-movie destination")
        self.assertIn("watched-movie destination", error)


if __name__ == "__main__":
    unittest.main()
