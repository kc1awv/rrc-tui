"""Debugging utilities for RRC protocol."""

from __future__ import annotations

import logging
from typing import Any

from .constants import (
    B_HELLO_CAPS,
    B_HELLO_NAME,
    B_HELLO_VER,
    B_JOINED_USERS,
    B_RES_ENCODING,
    B_RES_ID,
    B_RES_KIND,
    B_RES_SHA256,
    B_RES_SIZE,
    B_WELCOME_CAPS,
    B_WELCOME_HUB,
    B_WELCOME_VER,
    CAP_RESOURCE_ENVELOPE,
    K_BODY,
    K_ID,
    K_NICK,
    K_ROOM,
    K_SRC,
    K_T,
    K_TS,
    K_V,
    RES_KIND_BLOB,
    RES_KIND_MOTD,
    RES_KIND_NOTICE,
    T_ERROR,
    T_HELLO,
    T_JOIN,
    T_JOINED,
    T_MSG,
    T_NOTICE,
    T_PART,
    T_PARTED,
    T_PING,
    T_PONG,
    T_RESOURCE_ENVELOPE,
    T_WELCOME,
)

logger = logging.getLogger(__name__)


MESSAGE_TYPES = {
    T_HELLO: "HELLO",
    T_WELCOME: "WELCOME",
    T_JOIN: "JOIN",
    T_JOINED: "JOINED",
    T_PART: "PART",
    T_PARTED: "PARTED",
    T_MSG: "MSG",
    T_NOTICE: "NOTICE",
    T_PING: "PING",
    T_PONG: "PONG",
    T_ERROR: "ERROR",
    T_RESOURCE_ENVELOPE: "RESOURCE_ENVELOPE",
}

ENVELOPE_KEYS = {
    K_V: "VERSION",
    K_T: "TYPE",
    K_ID: "ID",
    K_TS: "TIMESTAMP",
    K_SRC: "SOURCE",
    K_ROOM: "ROOM",
    K_BODY: "BODY",
    K_NICK: "NICK",
}

HELLO_BODY_KEYS = {
    B_HELLO_NAME: "NAME",
    B_HELLO_VER: "VERSION",
    B_HELLO_CAPS: "CAPABILITIES",
}

WELCOME_BODY_KEYS = {
    B_WELCOME_HUB: "HUB_NAME",
    B_WELCOME_VER: "VERSION",
    B_WELCOME_CAPS: "CAPABILITIES",
}

JOINED_BODY_KEYS = {
    B_JOINED_USERS: "USERS",
}

RESOURCE_ENVELOPE_KEYS = {
    B_RES_ID: "RESOURCE_ID",
    B_RES_KIND: "KIND",
    B_RES_SIZE: "SIZE",
    B_RES_SHA256: "SHA256",
    B_RES_ENCODING: "ENCODING",
}

CAPABILITIES = {
    CAP_RESOURCE_ENVELOPE: "RESOURCE_ENVELOPE",
}

RESOURCE_KINDS = {
    RES_KIND_NOTICE: "notice",
    RES_KIND_MOTD: "motd",
    RES_KIND_BLOB: "blob",
}


def message_type_name(msg_type: int) -> str:
    """Get human-readable name for message type.

    Args:
        msg_type: Message type constant

    Returns:
        String name of message type or "UNKNOWN(n)"
    """
    return MESSAGE_TYPES.get(msg_type, f"UNKNOWN({msg_type})")


def envelope_key_name(key: int) -> str:
    """Get human-readable name for envelope key.

    Args:
        key: Envelope key constant

    Returns:
        String name of key or "UNKNOWN_KEY(n)"
    """
    return ENVELOPE_KEYS.get(key, f"UNKNOWN_KEY({key})")


def format_envelope_debug(envelope: dict[int, Any]) -> str:
    """Format envelope for debug logging.

    Args:
        envelope: RRC protocol envelope

    Returns:
        Human-readable string representation
    """
    parts = []
    msg_type = envelope.get(K_T, -1)
    type_name = message_type_name(msg_type)

    parts.append(f"Type: {type_name}")

    if K_ID in envelope:
        msg_id = envelope[K_ID]
        if isinstance(msg_id, bytes):
            parts.append(f"ID: {msg_id.hex()[:16]}...")
        else:
            parts.append(f"ID: {msg_id}")

    if K_ROOM in envelope:
        parts.append(f"Room: {envelope[K_ROOM]}")

    if K_SRC in envelope:
        src = envelope[K_SRC]
        if isinstance(src, bytes):
            parts.append(f"Src: {src.hex()[:16]}...")
        else:
            parts.append(f"Src: {src}")

    if K_NICK in envelope:
        parts.append(f"Nick: {envelope[K_NICK]}")

    if K_BODY in envelope:
        body = envelope[K_BODY]
        if isinstance(body, str):
            preview = body[:50] + "..." if len(body) > 50 else body
            parts.append(f"Body: '{preview}'")
        elif isinstance(body, dict):
            parts.append(f"Body: dict({len(body)} keys)")
        elif isinstance(body, list):
            parts.append(f"Body: list({len(body)} items)")
        else:
            parts.append(f"Body: {type(body).__name__}")

    return " | ".join(parts)


def log_envelope_debug(envelope: dict[int, Any], prefix: str = "") -> None:
    """Log envelope details at debug level.

    Args:
        envelope: RRC protocol envelope
        prefix: Optional prefix for log message (e.g., "RX" or "TX")
    """
    if logger.isEnabledFor(logging.DEBUG):
        msg = format_envelope_debug(envelope)
        if prefix:
            msg = f"{prefix}: {msg}"
        logger.debug(msg)


def validate_envelope_structure(envelope: dict[int, Any]) -> list[str]:
    """Validate envelope structure and return list of issues.

    Args:
        envelope: RRC protocol envelope

    Returns:
        List of validation issues (empty if valid)
    """
    issues = []

    if not isinstance(envelope, dict):
        issues.append("Envelope is not a dict")
        return issues

    required_keys = [K_V, K_T, K_ID, K_TS, K_SRC]
    for key in required_keys:
        if key not in envelope:
            key_name = envelope_key_name(key)
            issues.append(f"Missing required key: {key_name}")

    if K_V in envelope and not isinstance(envelope[K_V], int):
        issues.append(f"VERSION must be int, got {type(envelope[K_V]).__name__}")

    if K_T in envelope and not isinstance(envelope[K_T], int):
        issues.append(f"TYPE must be int, got {type(envelope[K_T]).__name__}")

    if K_ID in envelope and not isinstance(envelope[K_ID], bytes):
        issues.append(f"ID must be bytes, got {type(envelope[K_ID]).__name__}")

    if K_TS in envelope and not isinstance(envelope[K_TS], int):
        issues.append(f"TIMESTAMP must be int, got {type(envelope[K_TS]).__name__}")

    if K_SRC in envelope and not isinstance(envelope[K_SRC], bytes):
        issues.append(f"SOURCE must be bytes, got {type(envelope[K_SRC]).__name__}")

    if K_ROOM in envelope and not isinstance(envelope[K_ROOM], str):
        issues.append(f"ROOM must be str, got {type(envelope[K_ROOM]).__name__}")

    if K_NICK in envelope and not isinstance(envelope[K_NICK], str):
        issues.append(f"NICK must be str, got {type(envelope[K_NICK]).__name__}")

    return issues
