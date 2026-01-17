"""Entry point for RRC TUI client."""

import sys

from .config import FirstLaunchException, load_config
from .logging_manager import LogManager


def main():
    """Entry point for the TUI application."""
    try:
        config = load_config()
    except FirstLaunchException as e:
        print("\n=== Welcome to RRC TUI ===")
        print("\nA default configuration file has been created at:")
        print(f"  {e.config_path}")
        print(
            "\nPlease review and edit this file to configure your connection if desired."
        )
        print("\nOptionally, you can copy an existing RNS identity file to:")
        print(f"  {e.config_path.parent / 'identity'}")
        print(
            "\nIf no identity file is found, a new one will be created automatically"
        )
        print("when you first connect to a hub.")
        print("\nRun rrc-tui again after configuring to start the client.\n")
        sys.exit(0)

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
