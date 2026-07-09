import logging
import sys
import unittest
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.logger_config import configure_logging


class LoggerConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        """Clean up logging handlers before each test."""
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    def tearDown(self) -> None:
        """Clean up logging handlers after each test."""
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    def test_configure_logging_sets_root_logger_level(self) -> None:
        configure_logging(level=logging.DEBUG)
        root_logger = logging.getLogger()
        self.assertEqual(root_logger.level, logging.DEBUG)

    def test_configure_logging_sets_console_handler_level(self) -> None:
        configure_logging(level=logging.WARNING)
        root_logger = logging.getLogger()

        # Find the StreamHandler
        stream_handlers = [
            h for h in root_logger.handlers if isinstance(h, logging.StreamHandler)
        ]
        self.assertTrue(len(stream_handlers) > 0)
        self.assertEqual(stream_handlers[0].level, logging.WARNING)

    def test_configure_logging_removes_duplicate_handlers(self) -> None:
        configure_logging(level=logging.INFO)
        root_logger = logging.getLogger()
        first_handler_count = len(root_logger.handlers)

        configure_logging(level=logging.INFO)
        second_handler_count = len(root_logger.handlers)

        self.assertEqual(first_handler_count, second_handler_count)

    def test_configure_logging_adds_console_handler(self) -> None:
        configure_logging(level=logging.INFO)
        root_logger = logging.getLogger()

        stream_handlers = [
            h for h in root_logger.handlers if isinstance(h, logging.StreamHandler)
        ]
        self.assertTrue(len(stream_handlers) > 0)

    def test_logger_receives_configured_level(self) -> None:
        configure_logging(level=logging.INFO)
        logger = logging.getLogger("test_logger")

        # Create a handler that captures output
        string_io = StringIO()
        test_handler = logging.StreamHandler(string_io)
        test_handler.setLevel(logging.DEBUG)
        logger.addHandler(test_handler)

        # DEBUG messages should be suppressed by the root logger level
        logger.debug("debug message")
        self.assertEqual(string_io.getvalue(), "")

        # INFO messages should pass through
        logger.info("info message")
        self.assertIn("info message", string_io.getvalue())


if __name__ == "__main__":
    unittest.main()
