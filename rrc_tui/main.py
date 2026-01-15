"""Entry point for RRC TUI client."""

import sys

from .config import load_config
from .logging_manager import LogManager


def main():
    """Entry point for the TUI application."""
    config = load_config()
    log_manager = LogManager()
    log_manager.setup_logging(
        level=config.get("log_level", "INFO"),
        log_to_file=config.get("log_to_file", True),
        log_to_console=config.get("log_to_console", False),
        max_bytes=config.get("max_log_size_mb", 10) * 1024 * 1024,
        backup_count=config.get("log_backup_count", 5),
    )

    try:
        from .tui import run_textual_tui

        run_textual_tui()
    except ImportError:
        print(
            "Error: Textual library not installed. Install with: pip install textual",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
