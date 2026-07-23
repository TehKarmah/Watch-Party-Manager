"""Logging configuration for Watch Party Manager.

This module provides a centralized way to configure logging for the entire
application. It currently supports console logging, with the design prepared
to support file logging in the future.
"""

import logging


def configure_logging(level: int = logging.INFO) -> None:
    """Configure logging for the application.

    Sets up the root logger with a console handler. File logging can be added
    in the future by extending this function or adding new handlers.

    Args:
        level: The logging level (default: logging.INFO).
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to prevent duplicates if called multiple times
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)

    # Set consistent formatting across all loggers
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
