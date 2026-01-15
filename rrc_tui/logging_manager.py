"""Logging configuration for RRC TUI."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class LogManager:
    """Manages logging configuration for the application."""

    def __init__(self):
        self.log_dir = Path.home() / ".rrc-tui" / "logs"
        self.log_file = self.log_dir / "rrc-tui.log"

    def setup_logging(
        self,
        level: str = "INFO",
        log_to_file: bool = True,
        log_to_console: bool = False,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
    ) -> None:
        """Set up logging configuration.

        Args:
            level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            log_to_file: Whether to log to file
            log_to_console: Whether to log to console
            max_bytes: Maximum log file size before rotation
            backup_count: Number of backup files to keep
        """
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

        root_logger.handlers.clear()

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        if log_to_file:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                self.log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)

        if log_to_console:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            root_logger.addHandler(console_handler)
