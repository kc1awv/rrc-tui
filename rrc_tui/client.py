from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import RNS

from .codec import decode, encode
from .constants import (
    B_HELLO_CAPS,
    B_HELLO_NAME,
    B_HELLO_VER,
    B_RES_ENCODING,
    B_RES_ID,
    B_RES_KIND,
    B_RES_SHA256,
    B_RES_SIZE,
    B_WELCOME_LIMITS,
    CAP_RESOURCE_ENVELOPE,
    DEFAULT_MAX_MSG_BODY_BYTES,
    DEFAULT_MAX_NICK_BYTES,
    DEFAULT_MAX_ROOM_NAME_BYTES,
    DEFAULT_MAX_ROOMS_PER_SESSION,
    DEFAULT_RATE_LIMIT_MSGS_PER_MINUTE,
    K_BODY,
    K_ID,
    K_NICK,
    K_ROOM,
    K_T,
    L_MAX_MSG_BODY_BYTES,
    L_MAX_NICK_BYTES,
    L_MAX_ROOM_NAME_BYTES,
    L_MAX_ROOMS_PER_SESSION,
    L_RATE_LIMIT_MSGS_PER_MINUTE,
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
from .envelope import make_envelope, validate_envelope

logger = logging.getLogger(__name__)


class MessageTooLargeError(RuntimeError):
    """Raised when message exceeds link MDU."""

    pass


@dataclass
class _ResourceExpectation:
    """Tracks an expected incoming Resource transfer."""

    id: bytes
    kind: str
    size: int
    sha256: bytes | None
    encoding: str | None
    created_at: float
    expires_at: float
    room: str | None = None


@dataclass(frozen=True)
class ClientConfig:
    dest_name: str = "rrc.hub"
    max_resource_bytes: int = 262144
    resource_expectation_ttl_s: float = 30.0
    max_pending_resource_expectations: int = 8
    max_active_resources: int = 16
    hello_interval_s: float = 3.0
    hello_max_attempts: int = 3
    cleanup_existing_links: bool = True


class Client:
    def __init__(
        self,
        identity: RNS.Identity,
        config: ClientConfig | None = None,
        *,
        hello_body: dict[int, Any] | None = None,
        nickname: str | None = None,
    ) -> None:
        self.identity = identity
        self.config = config or ClientConfig()

        self.hello_body: dict[int, Any] = dict(hello_body or {})
        self.hello_body.setdefault(B_HELLO_NAME, "rrc-tui")
        self.hello_body.setdefault(B_HELLO_VER, "0.1.0")
        self.hello_body.setdefault(B_HELLO_CAPS, {CAP_RESOURCE_ENVELOPE: True})

        self.link: RNS.Link | None = None
        self.rooms: set[str] = set()

        self.max_nick_bytes = DEFAULT_MAX_NICK_BYTES
        self.max_room_name_bytes = DEFAULT_MAX_ROOM_NAME_BYTES
        self.max_msg_body_bytes = DEFAULT_MAX_MSG_BODY_BYTES
        self.max_rooms_per_session = DEFAULT_MAX_ROOMS_PER_SESSION
        self.rate_limit_msgs_per_minute = DEFAULT_RATE_LIMIT_MSGS_PER_MINUTE

        self._lock = threading.RLock()
        self._welcomed = threading.Event()

        self._resource_expectations: dict[bytes, _ResourceExpectation] = {}
        self._active_resources: set[RNS.Resource] = set()
        self._resource_to_expectation: dict[RNS.Resource, _ResourceExpectation] = {}

        self.on_message: Callable[[dict], None] | None = None
        self.on_notice: Callable[[dict], None] | None = None
        self.on_error: Callable[[dict], None] | None = None
        self.on_welcome: Callable[[dict], None] | None = None
        self.on_joined: Callable[[str, dict], None] | None = None
        self.on_parted: Callable[[str, dict], None] | None = None
        self.on_close: Callable[[], None] | None = None
        self.on_resource_warning: Callable[[str], None] | None = None
        self.on_pong: Callable[[dict], None] | None = None

        self._ping_thread: threading.Thread | None = None
        self._ping_stop = threading.Event()
        self._last_ping_time: float | None = None
        self.latency_ms: float | None = None

        self._nickname: str | None = None
        if nickname:
            self.set_nickname(nickname)

    def set_nickname(self, nickname: str | None) -> None:
        """Set the nickname with validation against hub limits."""
        if nickname is None:
            self._nickname = None
            return

        if not isinstance(nickname, str):
            raise ValueError(
                f"Nickname must be a string (got {type(nickname).__name__})"
            )

        nick_bytes = len(nickname.encode("utf-8"))
        if nick_bytes > self.max_nick_bytes:
            raise ValueError(
                f"Nickname too long: {nick_bytes} bytes exceeds hub limit of {self.max_nick_bytes} bytes"
            )

        self._nickname = nickname

    @property
    def nickname(self) -> str | None:
        """Get the current nickname."""
        return self._nickname

    @nickname.setter
    def nickname(self, value: str | None) -> None:
        """Set the nickname with validation."""
        self.set_nickname(value)

    def connect(
        self,
        hub_dest_hash: bytes,
        *,
        wait_for_welcome: bool = True,
        timeout_s: float = 20.0,
    ) -> None:
        self._welcomed.clear()

        RNS.Transport.request_path(hub_dest_hash)

        try:
            path_wait_deadline = time.monotonic() + min(5.0, float(timeout_s))
            sleep_interval = 0.05
            max_sleep = 0.5
            while time.monotonic() < path_wait_deadline:
                if RNS.Transport.has_path(hub_dest_hash):
                    break
                time.sleep(sleep_interval)
                sleep_interval = min(sleep_interval * 1.5, max_sleep)
        except Exception as e:
            logger.warning("Error during path wait: %s", e)

        recall_deadline = time.monotonic() + float(timeout_s)
        hub_identity: RNS.Identity | None = None
        sleep_interval = 0.05
        max_sleep = 0.5
        while time.monotonic() < recall_deadline:
            hub_identity = RNS.Identity.recall(hub_dest_hash)
            if hub_identity is not None:
                break
            time.sleep(sleep_interval)
            sleep_interval = min(sleep_interval * 1.5, max_sleep)

        if hub_identity is None:
            raise TimeoutError(
                "Could not recall hub identity from destination hash. "
                "Ensure: 1) The hub is online and announcing on the network, "
                "2) You have network connectivity to the Reticulum network, "
                "3) The hub hash is correct."
            )

        app_name, aspects = RNS.Destination.app_and_aspects_from_name(
            self.config.dest_name
        )

        hub_dest = RNS.Destination(
            hub_identity,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            app_name,
            *aspects,
        )

        if hub_dest.hash != hub_dest_hash:
            raise ValueError(
                "Hub hash does not match the destination name aspect. "
                f"Expected hash for '{self.config.dest_name}': {hub_dest.hash.hex()}, "
                f"but got: {hub_dest_hash.hex()}."
            )

        def _send_hello(link: RNS.Link) -> None:
            envelope = make_envelope(
                T_HELLO, src=self.identity.hash, body=self.hello_body
            )
            if self.nickname:
                envelope[K_NICK] = self.nickname
            payload = encode(envelope)
            RNS.Packet(link, payload).send()

        def _hello_loop(link: RNS.Link, deadline: float) -> None:
            hello_interval_s = self.config.hello_interval_s
            max_attempts = self.config.hello_max_attempts

            next_send = time.monotonic()
            attempts = 0

            while time.monotonic() < deadline and not self._welcomed.is_set():
                with self._lock:
                    if self.link is not link:
                        return

                now = time.monotonic()
                if attempts < max_attempts and now >= next_send:
                    try:
                        _send_hello(link)
                    except Exception as e:
                        logger.warning(
                            "Failed to send HELLO (attempt %d/%d): %s",
                            attempts + 1,
                            max_attempts,
                            e,
                        )
                    attempts += 1
                    next_send = now + hello_interval_s

                time.sleep(0.1)

        def _established(established_link: RNS.Link) -> None:
            logger.debug("Link established - setting resource callbacks")
            established_link.set_resource_strategy(RNS.Link.ACCEPT_APP)
            established_link.set_resource_callback(self._resource_advertised)
            established_link.set_resource_started_callback(self._resource_advertised)
            established_link.set_resource_concluded_callback(self._resource_concluded)

            try:
                established_link.identify(self.identity)
            except Exception as e:
                logger.error("Failed to identify on established link: %s", e)
                with contextlib.suppress(Exception):
                    established_link.teardown()
                return

            deadline = time.monotonic() + float(timeout_s)
            t = threading.Thread(
                target=_hello_loop,
                args=(established_link, deadline),
                name="rrc-client-hello",
                daemon=True,
            )
            t.start()

        def _closed(_: RNS.Link) -> None:
            with self._lock:
                self.link = None
                self.rooms.clear()
                active_resources = list(self._active_resources)
                self._resource_expectations.clear()
                self._active_resources.clear()
                self._resource_to_expectation.clear()

                for resource in active_resources:
                    try:
                        if hasattr(resource, "cancel") and callable(resource.cancel):
                            resource.cancel()
                    except Exception as e:
                        logger.debug(
                            "Error canceling resource in link closed callback: %s", e
                        )
                    finally:
                        try:
                            if hasattr(resource, "data") and resource.data:
                                resource.data.close()
                        except Exception as e:
                            logger.debug(
                                "Error closing resource data in link closed callback: %s",
                                e,
                            )

            if self.on_close:
                try:
                    self.on_close()
                except Exception as e:
                    logger.exception("Error in on_close callback: %s", e)

        if self.config.cleanup_existing_links:
            found_existing = False

            if hasattr(RNS.Transport, "active_links") and RNS.Transport.active_links:
                for existing_link in list(RNS.Transport.active_links):
                    try:
                        dest_hash = (
                            existing_link.destination.hash
                            if existing_link.destination
                            else None
                        )
                        if dest_hash == hub_dest_hash:
                            logger.info("Tearing down existing active link to same hub")
                            existing_link.teardown()
                            found_existing = True
                    except Exception as e:
                        logger.warning(
                            "Error checking/tearing down existing link: %s", e
                        )

            if found_existing:
                time.sleep(1.0)

        link = RNS.Link(
            hub_dest, established_callback=_established, closed_callback=_closed
        )
        link.set_packet_callback(lambda data, pkt: self._on_packet(data))

        with self._lock:
            self.link = link

        if wait_for_welcome:
            logger.debug("Waiting for WELCOME (timeout=%ss)...", timeout_s)
            welcome_timeout = float(timeout_s)
            if not self._welcomed.wait(timeout=welcome_timeout):
                logger.error("Timed out waiting for WELCOME from hub")
                raise TimeoutError(
                    f"Timed out waiting for WELCOME response from hub after {timeout_s}s."
                )
            logger.debug("WELCOME received")

    def close(self) -> None:
        self.stop_ping_thread()

        with self._lock:
            link = self.link
            self.link = None
            self.rooms.clear()
            self._resource_expectations.clear()

            active_resources = list(self._active_resources)
            self._active_resources.clear()
            self._resource_to_expectation.clear()

        for resource in active_resources:
            try:
                if hasattr(resource, "cancel") and callable(resource.cancel):
                    resource.cancel()
                if hasattr(resource, "data") and resource.data:
                    try:
                        resource.data.close()
                    except Exception as e:
                        logger.debug(
                            "Error closing resource data during cleanup: %s", e
                        )
            except Exception as e:
                logger.debug("Error canceling resource during cleanup: %s", e)

        if link is not None:
            try:
                link.teardown()
            except Exception as e:
                logger.debug("Error tearing down link during close: %s", e)

    def start_ping_thread(self, interval: float = 5.0) -> None:
        """Start a background thread that periodically pings the hub."""
        if self._ping_thread is not None:
            return

        self._ping_stop.clear()

        def _ping_loop() -> None:
            while not self._ping_stop.wait(timeout=interval):
                with self._lock:
                    if self.link and self.link.status == RNS.Link.ACTIVE:
                        try:
                            self.ping()
                        except Exception as e:
                            logger.debug("Error sending ping: %s", e)
                    else:
                        break

        self._ping_thread = threading.Thread(
            target=_ping_loop, name="rrc-client-ping", daemon=True
        )
        self._ping_thread.start()

    def stop_ping_thread(self) -> None:
        """Stop the ping thread."""
        if self._ping_thread is None:
            return

        self._ping_stop.set()
        if self._ping_thread.is_alive():
            self._ping_thread.join(timeout=2.0)
        self._ping_thread = None
        self._last_ping_time = None
        self.latency_ms = None

    def join(self, room: str, *, key: str | None = None) -> None:
        if not isinstance(room, str):
            raise ValueError(f"Room name must be a string (got {type(room).__name__})")
        r = room.strip().lower()
        if not r:
            raise ValueError("Room name cannot be empty.")

        room_bytes = len(r.encode("utf-8"))
        if room_bytes > self.max_room_name_bytes:
            raise ValueError(
                f"Room name too long: {room_bytes} bytes exceeds hub limit of {self.max_room_name_bytes} bytes"
            )

        with self._lock:
            if len(self.rooms) >= self.max_rooms_per_session:
                raise ValueError(
                    f"Cannot join more rooms: already in {len(self.rooms)} rooms (hub limit: {self.max_rooms_per_session})"
                )

        body: Any = key if (isinstance(key, str) and key) else None
        self._send(make_envelope(T_JOIN, src=self.identity.hash, room=r, body=body))

    def part(self, room: str) -> None:
        if not isinstance(room, str):
            raise ValueError(f"Room name must be a string (got {type(room).__name__})")
        r = room.strip().lower()
        if not r:
            raise ValueError("Room name cannot be empty.")
        self._send(make_envelope(T_PART, src=self.identity.hash, room=r))
        with self._lock:
            self.rooms.discard(r)

    def msg(self, room: str, text: str) -> bytes:
        if not isinstance(room, str):
            raise ValueError(f"Room name must be a string (got {type(room).__name__})")
        if not isinstance(text, str):
            raise ValueError(
                f"Message text must be a string (got {type(text).__name__})"
            )
        r = room.strip().lower()
        if not r:
            raise ValueError("Room name cannot be empty.")
        if not text.strip():
            raise ValueError("Message text cannot be empty.")

        msg_bytes = len(text.encode("utf-8"))
        if msg_bytes > self.max_msg_body_bytes:
            raise ValueError(
                f"Message too long: {msg_bytes} bytes exceeds hub limit of {self.max_msg_body_bytes} bytes"
            )

        env = make_envelope(T_MSG, src=self.identity.hash, room=r, body=text)
        if self.nickname:
            env[K_NICK] = self.nickname
        self._send(env)
        mid = env.get(K_ID)
        if not isinstance(mid, (bytes, bytearray)):
            raise TypeError("message id (K_ID) must be bytes")
        return bytes(mid)

    def ping(self) -> None:
        """Send a PING to the server."""
        self._last_ping_time = time.monotonic()
        self._send(make_envelope(T_PING, src=self.identity.hash))

    def _packet_would_fit(self, link: RNS.Link, payload: bytes) -> bool:
        """Check if packet would fit within link MDU."""
        try:
            pkt = RNS.Packet(link, payload)
            pkt.pack()
            return True
        except Exception:
            return False

    def _cleanup_expired_expectations(self) -> None:
        """Remove expired resource expectations."""
        now = time.monotonic()
        with self._lock:
            expired = [
                rid
                for rid, exp in self._resource_expectations.items()
                if now >= exp.expires_at
            ]
            for rid in expired:
                del self._resource_expectations[rid]

    def _find_resource_expectation(self, size: int) -> _ResourceExpectation | None:
        """Find matching resource expectation by size."""
        self._cleanup_expired_expectations()

        with self._lock:
            for _rid, exp in list(self._resource_expectations.items()):
                if exp.size == size:
                    return exp
        return None

    def _resource_advertised(self, resource: RNS.Resource) -> bool:
        """Callback when a Resource is advertised. Returns True to accept, False to reject."""
        try:
            if hasattr(resource, "get_data_size"):
                size = resource.get_data_size()
            elif hasattr(resource, "total_size"):
                size = resource.total_size
            elif hasattr(resource, "size"):
                size = resource.size
            else:
                logger.error("Resource object has no size attribute")
                return False

            logger.debug(f"Resource advertised: size={size}")
        except Exception as e:
            logger.error(f"Error getting resource size: {e}")
            return False

        if size > self.config.max_resource_bytes:
            logger.debug(f"Rejecting resource: size {size} exceeds max")
            return False

        with self._lock:
            if len(self._active_resources) >= self.config.max_active_resources:
                logger.warning("Rejecting resource: too many active transfers")
                return False

        exp = self._find_resource_expectation(size)
        if not exp:
            logger.warning("Resource advertised without matching expectation")
            with self._lock:
                self._active_resources.add(resource)
            logger.info(f"Accepted speculative resource transfer: size={size}")
            return True

        with self._lock:
            self._active_resources.add(resource)
            self._resource_to_expectation[resource] = exp

        logger.info(f"Accepted resource transfer: kind={exp.kind}, size={size}")
        return True

    def _resource_concluded(self, resource: RNS.Resource) -> None:
        """Callback when a Resource transfer completes."""
        logger.debug(f"Resource concluded, status={resource.status}")
        with self._lock:
            self._active_resources.discard(resource)
            matched_exp = self._resource_to_expectation.pop(resource, None)

        if not matched_exp:
            size = (
                resource.total_size
                if hasattr(resource, "total_size")
                else resource.size
            )
            matched_exp = self._find_resource_expectation(size)
            if not matched_exp:
                logger.warning("No expectation found for concluded resource")
                try:
                    if hasattr(resource, "data") and resource.data:
                        resource.data.close()
                except Exception as e:
                    logger.debug("Error closing unexpected resource data: %s", e)
                return

        with self._lock:
            for rid, exp in list(self._resource_expectations.items()):
                if exp == matched_exp:
                    self._resource_expectations.pop(rid, None)
                    break

        if resource.status != RNS.Resource.COMPLETE:
            logger.warning(f"Resource transfer incomplete: status={resource.status}")
            try:
                if hasattr(resource, "data") and resource.data:
                    resource.data.close()
            except Exception as e:
                logger.debug("Error closing incomplete resource data: %s", e)
            return

        data = None
        try:
            data = resource.data.read()
        except Exception as e:
            logger.warning("Failed to read resource data: %s", e)
        finally:
            try:
                if hasattr(resource, "data") and resource.data:
                    resource.data.close()
            except Exception as e:
                logger.debug("Error closing resource data: %s", e)

        if data is None:
            return

        if matched_exp.sha256:
            computed = hashlib.sha256(data).digest()
            if computed != matched_exp.sha256:
                logger.warning("Resource SHA256 mismatch")
                return

        if matched_exp.kind in (RES_KIND_NOTICE, RES_KIND_MOTD):
            try:
                encoding = matched_exp.encoding or "utf-8"
                text = data.decode(encoding)
                logger.info(f"Received {matched_exp.kind.upper()} resource")
                env = {
                    K_T: T_NOTICE,
                    K_BODY: text,
                    K_ROOM: matched_exp.room,
                }
                if self.on_notice:
                    try:
                        self.on_notice(env)
                    except Exception as e:
                        logger.exception("Error in on_notice callback: %s", e)
            except UnicodeDecodeError as e:
                logger.warning(f"Failed to decode {matched_exp.kind} resource: %s", e)
            except Exception as e:
                logger.exception(f"Error processing {matched_exp.kind} resource: %s", e)

    def _send(self, env: dict) -> None:
        with self._lock:
            link = self.link
        if link is None:
            raise RuntimeError("Not connected to hub.")
        payload = encode(env)

        if not self._packet_would_fit(link, payload):
            if self.on_resource_warning:
                warning = "Message is too large to send."
                with contextlib.suppress(Exception):
                    self.on_resource_warning(warning)
            raise MessageTooLargeError("Message exceeds link MDU")

        RNS.Packet(link, payload).send()

    def _on_packet(self, data: bytes) -> None:
        try:
            env = decode(data)
            validate_envelope(env)
        except Exception as e:
            logger.debug("Failed to decode/validate packet: %s", e)
            return

        t = env.get(K_T)

        if t == T_PING:
            body = env.get(K_BODY)
            with contextlib.suppress(Exception):
                self._send(make_envelope(T_PONG, src=self.identity.hash, body=body))
            return

        if t == T_PONG:
            if self.on_pong:
                with contextlib.suppress(Exception):
                    self.on_pong(env)
            return

        if t == T_RESOURCE_ENVELOPE:
            body = env.get(K_BODY)
            if not isinstance(body, dict):
                return

            try:
                rid = body.get(B_RES_ID)
                kind = body.get(B_RES_KIND)
                size = body.get(B_RES_SIZE)
                sha256 = body.get(B_RES_SHA256)
                encoding = body.get(B_RES_ENCODING)

                if not isinstance(rid, (bytes, bytearray)):
                    return
                if not isinstance(kind, str):
                    return
                if not isinstance(size, int) or size <= 0:
                    return
                if sha256 is not None and not isinstance(sha256, (bytes, bytearray)):
                    return
                if encoding is not None and not isinstance(encoding, str):
                    return

                if size > self.config.max_resource_bytes:
                    return

                now = time.monotonic()
                room = env.get(K_ROOM)

                with self._lock:
                    if (
                        len(self._resource_expectations)
                        >= self.config.max_pending_resource_expectations
                    ):
                        oldest_rid = min(
                            self._resource_expectations.keys(),
                            key=lambda r: self._resource_expectations[r].created_at,
                        )
                        del self._resource_expectations[oldest_rid]

                    self._resource_expectations[bytes(rid)] = _ResourceExpectation(
                        id=bytes(rid),
                        kind=kind,
                        size=size,
                        sha256=bytes(sha256) if sha256 else None,
                        encoding=encoding,
                        created_at=now,
                        expires_at=now + self.config.resource_expectation_ttl_s,
                        room=room if isinstance(room, str) else None,
                    )
                    logger.debug(
                        f"Stored resource expectation: kind={kind}, size={size}"
                    )
            except Exception as e:
                logger.warning("Failed to process resource envelope: %s", e)
            return

        if t == T_WELCOME:
            logger.debug("Received T_WELCOME")

            body = env.get(K_BODY)
            if isinstance(body, dict) and B_WELCOME_LIMITS in body:
                limits = body[B_WELCOME_LIMITS]
                if isinstance(limits, dict):
                    with self._lock:
                        if L_MAX_NICK_BYTES in limits:
                            self.max_nick_bytes = int(limits[L_MAX_NICK_BYTES])
                        if L_MAX_ROOM_NAME_BYTES in limits:
                            self.max_room_name_bytes = int(
                                limits[L_MAX_ROOM_NAME_BYTES]
                            )
                        if L_MAX_MSG_BODY_BYTES in limits:
                            self.max_msg_body_bytes = int(limits[L_MAX_MSG_BODY_BYTES])
                        if L_MAX_ROOMS_PER_SESSION in limits:
                            self.max_rooms_per_session = int(
                                limits[L_MAX_ROOMS_PER_SESSION]
                            )
                        if L_RATE_LIMIT_MSGS_PER_MINUTE in limits:
                            self.rate_limit_msgs_per_minute = int(
                                limits[L_RATE_LIMIT_MSGS_PER_MINUTE]
                            )
                    logger.debug(
                        "Hub limits: nick=%d, room=%d, msg=%d, max_rooms=%d, rate=%d/min",
                        self.max_nick_bytes,
                        self.max_room_name_bytes,
                        self.max_msg_body_bytes,
                        self.max_rooms_per_session,
                        self.rate_limit_msgs_per_minute,
                    )

            self._welcomed.set()
            if self.on_welcome:
                try:
                    self.on_welcome(env)
                except Exception as e:
                    logger.exception("Error in on_welcome callback: %s", e)
            return

        if t == T_JOINED:
            room = env.get(K_ROOM)
            if isinstance(room, str) and room:
                r = room.strip().lower()
                with self._lock:
                    self.rooms.add(r)
                if self.on_joined:
                    try:
                        self.on_joined(r, env)
                    except Exception as e:
                        logger.exception("Error in on_joined callback: %s", e)
            return

        if t == T_PARTED:
            room = env.get(K_ROOM)
            if isinstance(room, str) and room:
                r = room.strip().lower()
                with self._lock:
                    self.rooms.discard(r)
                if self.on_parted:
                    try:
                        self.on_parted(r, env)
                    except Exception as e:
                        logger.exception("Error in on_parted callback: %s", e)
            return

        if t == T_MSG:
            if self.on_message:
                try:
                    self.on_message(env)
                except Exception as e:
                    logger.exception("Error in on_message callback: %s", e)
            return

        if t == T_NOTICE:
            if self.on_notice:
                try:
                    self.on_notice(env)
                except Exception as e:
                    logger.exception("Error in on_notice callback: %s", e)
            return

        if t == T_ERROR:
            if self.on_error:
                try:
                    self.on_error(env)
                except Exception as e:
                    logger.exception("Error in on_error callback: %s", e)
            return
