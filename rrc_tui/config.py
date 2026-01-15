"""Configuration file management for RRC TUI."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _expand_path(p: str) -> str:
    """Expand ~ and environment variables in path."""
    return os.path.expanduser(os.path.expandvars(p))


def get_config_dir() -> Path:
    """Find existing config directory or return default.

    Searches in order:
    1. /etc/rrc-tui
    2. ~/.config/rrc-tui
    3. ~/.rrc-tui

    Returns:
        Path to config directory (may not exist yet)
    """
    search_paths = [
        Path("/etc/rrc-tui"),
        Path(_expand_path("~/.config/rrc-tui")),
        Path(_expand_path("~/.rrc-tui")),
    ]

    for path in search_paths:
        if path.exists() and path.is_dir():
            logger.debug(f"Found existing config directory: {path}")
            return path

    default_path = Path(_expand_path("~/.rrc-tui"))
    logger.debug(f"No existing config directory found, using default: {default_path}")
    return default_path


def get_config_path() -> Path:
    """Get path to TUI config file."""
    return get_config_dir() / "config.json"


def get_default_config() -> dict[str, Any]:
    """Get default configuration values.

    Returns:
        Dictionary with default configuration
    """
    config_dir = get_config_dir()
    default_identity_path = str(config_dir / "identity")

    return {
        "hub_hash": "",
        "nickname": "",
        "auto_join_room": "",
        "identity_path": default_identity_path,
        "dest_name": "rrc.hub",
        "configdir": "",
        "log_level": "INFO",
        "log_to_file": True,
        "log_to_console": False,
        "max_log_size_mb": 10,
        "log_backup_count": 5,
        "rate_limit_enabled": True,
        "rate_warning_threshold": 0.8,
        "input_history_size": 50,
        "save_input_history": True,
        "max_messages_per_room": 500,
        "show_timestamps": True,
        "timestamp_format": "%H:%M:%S",
        "auto_reconnect": True,
        "reconnect_delay_seconds": 5,
        "connection_timeout_seconds": 30,
        "ping_interval_seconds": 60,
    }


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate and sanitize config values.

    Args:
        config: Configuration dictionary to validate

    Returns:
        Validated and sanitized configuration
    """
    defaults = get_default_config()

    int_fields = [
        "max_log_size_mb",
        "log_backup_count",
        "input_history_size",
        "max_messages_per_room",
        "reconnect_delay_seconds",
        "connection_timeout_seconds",
        "ping_interval_seconds",
    ]
    for field in int_fields:
        if field in config:
            try:
                config[field] = int(config[field])
                if config[field] < 0:
                    config[field] = defaults[field]
            except (ValueError, TypeError):
                config[field] = defaults[field]

    float_fields = ["rate_warning_threshold"]
    for field in float_fields:
        if field in config:
            try:
                config[field] = float(config[field])
                if not (0.0 <= config[field] <= 1.0):
                    config[field] = defaults[field]
            except (ValueError, TypeError):
                config[field] = defaults[field]

    bool_fields = [
        "log_to_file",
        "log_to_console",
        "rate_limit_enabled",
        "save_input_history",
        "show_timestamps",
        "auto_reconnect",
    ]
    for field in bool_fields:
        if field in config:
            if not isinstance(config[field], bool):
                config[field] = defaults[field]

    str_fields = [
        "hub_hash",
        "nickname",
        "auto_join_room",
        "identity_path",
        "dest_name",
        "configdir",
        "log_level",
        "timestamp_format",
    ]
    for field in str_fields:
        if field in config:
            if not isinstance(config[field], str):
                config[field] = defaults[field]

    valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if config.get("log_level", "").upper() not in valid_log_levels:
        config["log_level"] = defaults["log_level"]

    return config


def load_config() -> dict[str, Any]:
    """Load saved configuration.

    Returns:
        Configuration dictionary with defaults filled in and validated
    """
    config_path = get_config_path()
    saved_config = {}

    if config_path.exists():
        logger.info(f"Loading config from {config_path}")
        try:
            with open(config_path, encoding="utf-8") as f:
                saved_config = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load config from {config_path}: {e}")
    else:
        logger.info(
            f"No config file found, will use defaults. Config will be saved to {config_path}"
        )

    default_config = get_default_config()
    default_config.update(saved_config)

    return validate_config(default_config)


def save_config(config: dict[str, Any]) -> None:
    """Save configuration.

    Args:
        config: Configuration dictionary to save
    """
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        raise RuntimeError(f"Failed to save config: {e}") from e
