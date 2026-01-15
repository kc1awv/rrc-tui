"""Utility functions for RRC TUI."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import RNS

logger = logging.getLogger(__name__)


def get_identity_path(config_identity_path: str | None = None) -> Path:
    """Get the path to the identity file.

    Searches for existing identity file in:
    1. /etc/rrc-tui/identity
    2. ~/.config/rrc-tui/identity
    3. ~/.rrc-tui/identity

    If config specifies identity_path, use that if it exists.
    Otherwise, use the first existing identity, or default to ~/.rrc-tui/identity.

    Args:
        config_identity_path: Optional path from config

    Returns:
        Path to identity file
    """
    if config_identity_path:
        config_path = Path(os.path.expanduser(os.path.expandvars(config_identity_path)))
        if config_path.exists():
            logger.debug(f"Using identity path from config: {config_path}")
            return config_path

    search_paths = [
        Path("/etc/rrc-tui/identity"),
        Path(os.path.expanduser("~/.config/rrc-tui/identity")),
        Path(os.path.expanduser("~/.rrc-tui/identity")),
    ]

    for path in search_paths:
        if path.exists():
            logger.debug(f"Found existing identity at: {path}")
            return path

    default_path = Path(os.path.expanduser("~/.rrc-tui/identity"))
    logger.debug(f"No existing identity found, using default: {default_path}")
    return default_path


def load_or_create_identity(identity_path: str) -> RNS.Identity:
    """Load identity from file or create new one.

    Args:
        identity_path: Path to identity file (can be relative or use ~ expansion)

    Returns:
        RNS.Identity instance
    """
    path = get_identity_path(identity_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            identity = RNS.Identity.from_file(str(path))
            logger.info(f"Loaded identity from {path}")
            return identity
        except Exception as e:
            logger.warning(f"Failed to load identity from {path}: {e}")
            logger.info("Creating new identity")

    identity = RNS.Identity()
    try:
        identity.to_file(str(path))
        logger.info(f"Created and saved new identity to {path}")
    except Exception as e:
        logger.error(f"Failed to save identity to {path}: {e}")

    return identity


def normalize_room_name(room: str) -> str:
    """Normalize room name to lowercase.

    Args:
        room: Room name

    Returns:
        Normalized room name
    """
    return room.lower().strip()


def sanitize_display_name(name: str, max_length: int = 50, strict: bool = False) -> str:
    """Sanitize display name for UI.

    Args:
        name: Display name to sanitize
        max_length: Maximum allowed length
        strict: If True, only allow alphanumeric, spaces, and common punctuation

    Returns:
        Sanitized name
    """
    if not name:
        return ""

    name = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", name)
    name = " ".join(name.split())

    if strict:
        name = re.sub(r"[^a-zA-Z0-9 \-_.\'()]", "", name)
        name = " ".join(name.split())

    if len(name) > max_length:
        name = name[:max_length]

    return name


def format_identity_hash(identity_hash: bytes | str) -> str:
    """Format identity hash for display.

    Args:
        identity_hash: Identity hash as bytes or hex string

    Returns:
        Formatted hash string
    """
    if isinstance(identity_hash, bytes):
        identity_hash = identity_hash.hex()

    if len(identity_hash) > 16:
        return f"{identity_hash[:8]}...{identity_hash[-8:]}"
    return identity_hash


def parse_hash(text: str) -> bytes:
    """Parse hash from text input.

    Args:
        text: Hash as hex string

    Returns:
        Hash as bytes

    Raises:
        ValueError: If hash is invalid
    """
    s = str(text).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    s = "".join(ch for ch in s if not ch.isspace())
    try:
        b = bytes.fromhex(s)
    except Exception as e:
        raise ValueError(f"invalid hash {text!r}: {e}") from e
    if len(b) != 16:
        raise ValueError(f"destination hash must be 16 bytes (got {len(b)})")
    return b
