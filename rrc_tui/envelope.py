from __future__ import annotations

import os
import time

from .constants import K_BODY, K_ID, K_ROOM, K_SRC, K_T, K_TS, K_V, RRC_VERSION


def now_ms() -> int:
    return int(time.time() * 1000)


def msg_id() -> bytes:
    return os.urandom(8)


def make_envelope(
    msg_type: int,
    *,
    src: bytes,
    room: str | None = None,
    body=None,
    mid: bytes | None = None,
    ts: int | None = None,
) -> dict:
    env: dict[int, object] = {
        K_V: RRC_VERSION,
        K_T: int(msg_type),
        K_ID: mid or msg_id(),
        K_TS: ts or now_ms(),
        K_SRC: src,
    }
    if room is not None:
        env[K_ROOM] = room
    if body is not None:
        env[K_BODY] = body
    return env


def validate_envelope(env: dict) -> None:
    if not isinstance(env, dict):
        raise TypeError("envelope must be a CBOR map (dict)")

    for k in env.keys():
        if not isinstance(k, int):
            raise TypeError("envelope keys must be integers")
        if k < 0:
            raise ValueError("envelope keys must be unsigned integers")

    for k in (K_V, K_T, K_ID, K_TS, K_SRC):
        if k not in env:
            raise ValueError(f"envelope missing required key {k}")

    version = env.get(K_V)
    if not isinstance(version, int):
        raise TypeError("envelope version must be an integer")
    if version != RRC_VERSION:
        raise ValueError(f"unsupported envelope version {version}")

    msg_type = env.get(K_T)
    if not isinstance(msg_type, int):
        raise TypeError("envelope message type must be an integer")
    if msg_type < 0:
        raise ValueError("envelope message type must be unsigned")

    mid = env.get(K_ID)
    if not isinstance(mid, bytes):
        raise TypeError("envelope message ID must be bytes")

    timestamp = env.get(K_TS)
    if not isinstance(timestamp, int):
        raise TypeError("envelope timestamp must be an integer")
    if timestamp < 0:
        raise ValueError("envelope timestamp must be unsigned")

    src = env.get(K_SRC)
    if not isinstance(src, bytes):
        raise TypeError("envelope source must be bytes")

    if K_ROOM in env:
        room = env[K_ROOM]
        if not isinstance(room, str):
            raise TypeError("envelope room must be a string")
