import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_party import WatchParty, WatchPartyStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WatchPartyModelTests(unittest.TestCase):
    def test_valid_watch_party(self) -> None:
        scheduled_at = utc_now()
        watch_party = WatchParty(
            id=1,
            watch_item_id=42,
            scheduled_at=scheduled_at,
            guild_id=100,
            channel_id=200,
        )

        self.assertEqual(watch_party.id, 1)
        self.assertEqual(watch_party.watch_item_id, 42)
        self.assertEqual(watch_party.scheduled_at, scheduled_at)
        self.assertEqual(watch_party.guild_id, 100)
        self.assertEqual(watch_party.channel_id, 200)
        self.assertEqual(watch_party.status, WatchPartyStatus.SCHEDULED)

    def test_channel_id_defaults_to_none(self) -> None:
        watch_party = WatchParty(id=1, watch_item_id=1, scheduled_at=utc_now(), guild_id=1)

        self.assertIsNone(watch_party.channel_id)

    def test_created_at_defaults_to_now(self) -> None:
        before = utc_now()
        watch_party = WatchParty(id=1, watch_item_id=1, scheduled_at=utc_now(), guild_id=1)
        after = utc_now()

        self.assertLessEqual(before, watch_party.created_at)
        self.assertLessEqual(watch_party.created_at, after)

    def test_rejects_non_positive_id(self) -> None:
        with self.assertRaises(ValueError):
            WatchParty(id=0, watch_item_id=1, scheduled_at=utc_now(), guild_id=1)

    def test_rejects_non_positive_watch_item_id(self) -> None:
        with self.assertRaises(ValueError):
            WatchParty(id=1, watch_item_id=0, scheduled_at=utc_now(), guild_id=1)

    def test_rejects_non_positive_guild_id(self) -> None:
        with self.assertRaises(ValueError):
            WatchParty(id=1, watch_item_id=1, scheduled_at=utc_now(), guild_id=0)

    def test_rejects_non_positive_channel_id_when_provided(self) -> None:
        with self.assertRaises(ValueError):
            WatchParty(id=1, watch_item_id=1, scheduled_at=utc_now(), guild_id=1, channel_id=0)

    def test_rejects_naive_scheduled_at(self) -> None:
        with self.assertRaises(ValueError):
            WatchParty(id=1, watch_item_id=1, scheduled_at=datetime(2026, 7, 20, 18, 0), guild_id=1)

    def test_rejects_naive_created_at(self) -> None:
        with self.assertRaises(ValueError):
            WatchParty(
                id=1,
                watch_item_id=1,
                scheduled_at=utc_now(),
                guild_id=1,
                created_at=datetime(2026, 7, 20, 18, 0),
            )


class WatchPartyLifecycleFieldsTests(unittest.TestCase):
    """Watch Party Lifecycle additions: duration_minutes, vote_round_id,
    description_override, and the derived ends_at property.
    """

    def test_new_fields_default_to_none(self) -> None:
        watch_party = WatchParty(id=1, watch_item_id=1, scheduled_at=utc_now(), guild_id=1)

        self.assertIsNone(watch_party.duration_minutes)
        self.assertIsNone(watch_party.vote_round_id)
        self.assertIsNone(watch_party.description_override)
        self.assertIsNone(watch_party.ends_at)

    def test_ends_at_is_scheduled_at_plus_duration(self) -> None:
        scheduled_at = utc_now()
        watch_party = WatchParty(
            id=1, watch_item_id=1, scheduled_at=scheduled_at, guild_id=1, duration_minutes=150
        )

        self.assertEqual(watch_party.ends_at, scheduled_at + timedelta(minutes=150))

    def test_rejects_non_positive_duration_minutes(self) -> None:
        with self.assertRaises(ValueError):
            WatchParty(id=1, watch_item_id=1, scheduled_at=utc_now(), guild_id=1, duration_minutes=0)

    def test_rejects_non_positive_vote_round_id(self) -> None:
        with self.assertRaises(ValueError):
            WatchParty(id=1, watch_item_id=1, scheduled_at=utc_now(), guild_id=1, vote_round_id=0)

    def test_description_override_is_trimmed(self) -> None:
        watch_party = WatchParty(
            id=1, watch_item_id=1, scheduled_at=utc_now(), guild_id=1, description_override="  Bring snacks!  "
        )

        self.assertEqual(watch_party.description_override, "Bring snacks!")

    def test_blank_description_override_normalizes_to_none(self) -> None:
        watch_party = WatchParty(
            id=1, watch_item_id=1, scheduled_at=utc_now(), guild_id=1, description_override="   "
        )

        self.assertIsNone(watch_party.description_override)


class WatchPartyWithChangesTests(unittest.TestCase):
    def test_with_changes_returns_a_new_instance(self) -> None:
        original = WatchParty(id=1, watch_item_id=1, scheduled_at=utc_now(), guild_id=1)
        new_time = utc_now()

        updated = original.with_changes(scheduled_at=new_time)

        self.assertIsNot(updated, original)
        self.assertEqual(updated.scheduled_at, new_time)
        self.assertEqual(updated.id, original.id)

    def test_with_changes_does_not_mutate_the_original(self) -> None:
        original_time = utc_now()
        original = WatchParty(id=1, watch_item_id=1, scheduled_at=original_time, guild_id=1)

        original.with_changes(scheduled_at=utc_now())

        self.assertEqual(original.scheduled_at, original_time)

    def test_with_changes_revalidates_the_new_value(self) -> None:
        original = WatchParty(id=1, watch_item_id=1, scheduled_at=utc_now(), guild_id=1)

        with self.assertRaises(ValueError):
            original.with_changes(scheduled_at=datetime(2026, 7, 20, 18, 0))


if __name__ == "__main__":
    unittest.main()
