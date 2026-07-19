from datetime import timedelta
import unittest

from watch_party_manager.scheduler.retry_policy import RetryPolicy


class RetryPolicyTests(unittest.TestCase):
    def test_default_delays_are_one_five_and_fifteen_minutes(self) -> None:
        policy = RetryPolicy()

        self.assertEqual(
            policy.delays,
            (
                timedelta(minutes=1),
                timedelta(minutes=5),
                timedelta(minutes=15),
            ),
        )

    def test_maximum_attempts_matches_delay_count(self) -> None:
        self.assertEqual(RetryPolicy().maximum_attempts, 3)

    def test_delay_after_failure_uses_attempt_number(self) -> None:
        policy = RetryPolicy()

        self.assertEqual(policy.delay_after_failure(1), timedelta(minutes=1))
        self.assertEqual(policy.delay_after_failure(2), timedelta(minutes=5))
        self.assertEqual(policy.delay_after_failure(3), timedelta(minutes=15))

    def test_delay_after_failure_returns_none_after_retries_are_exhausted(self) -> None:
        self.assertIsNone(RetryPolicy().delay_after_failure(4))

    def test_delay_after_failure_rejects_nonpositive_attempt_count(self) -> None:
        with self.assertRaises(ValueError):
            RetryPolicy().delay_after_failure(0)


if __name__ == "__main__":
    unittest.main()
