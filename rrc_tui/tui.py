"""Textual TUI interface for RRC client."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import RNS
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from .client import Client, ClientConfig
from .config import load_config
from .constants import (
    B_WELCOME_HUB,
    B_WELCOME_LIMITS,
    K_BODY,
    K_ID,
    K_NICK,
    K_ROOM,
    K_SRC,
    L_MAX_MSG_BODY_BYTES,
    L_MAX_NICK_BYTES,
    L_MAX_ROOM_NAME_BYTES,
    L_MAX_ROOMS_PER_SESSION,
    L_RATE_LIMIT_MSGS_PER_MINUTE,
)
from .utils import (
    format_identity_hash,
    load_or_create_identity,
    normalize_room_name,
    parse_hash,
    sanitize_display_name,
)

logger = logging.getLogger(__name__)

MAX_HUB_ANNOUNCE_DATA_BYTES = 10240
MAX_HUB_NAME_LENGTH = 200
MAX_MESSAGES_PER_ROOM_DEFAULT = 500


@dataclass
class RoomState:
    """Consolidated state for a chat room."""

    messages: list[tuple[str, str]] = field(default_factory=list)
    users: set[str] = field(default_factory=set)
    mode: str = ""
    topic: str = ""
    unread_count: int = 0


class PendingMessageTracker:
    """Thread-safe tracker for pending message confirmations."""

    def __init__(self, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds
        self._pending: dict[bytes, tuple[str, str, float]] = {}
        self._lock = threading.Lock()
        self._checker_running = False
        self._checker_thread: threading.Thread | None = None

    def add(self, msg_id: bytes, room: str, text: str) -> None:
        """Add a pending message."""
        with self._lock:
            self._pending[msg_id] = (room, text, time.time())

    def confirm(self, msg_id: bytes) -> tuple[str, str, float] | None:
        """Confirm and remove a pending message. Returns the pending data if found."""
        with self._lock:
            return self._pending.pop(msg_id, None)

    def get_timed_out(self) -> list[tuple[bytes, str, str]]:
        """Get and remove all timed out messages."""
        current_time = time.time()
        timed_out = []
        with self._lock:
            expired = [
                (msg_id, room, text)
                for msg_id, (room, text, sent_time) in self._pending.items()
                if current_time - sent_time > self.timeout_seconds
            ]
            for msg_id, _, _ in expired:
                del self._pending[msg_id]
            timed_out = expired
        return timed_out

    def clear(self) -> None:
        """Clear all pending messages."""
        with self._lock:
            self._pending.clear()

    def start_checker(
        self, on_timeout_callback: Callable[[bytes, str, str], None]
    ) -> None:
        """Start the background timeout checker thread."""
        if self._checker_running:
            return

        self._checker_running = True

        def timeout_checker():
            while self._checker_running:
                try:
                    timed_out = self.get_timed_out()
                    for msg_id, room, text in timed_out:
                        on_timeout_callback(msg_id, room, text)
                    time.sleep(1.0)
                except Exception as e:
                    logger.error(f"Error in timeout checker: {e}")
                    time.sleep(1.0)

        self._checker_thread = threading.Thread(
            target=timeout_checker, daemon=True, name="msg-timeout-checker"
        )
        self._checker_thread.start()

    def stop_checker(self) -> None:
        """Stop the background timeout checker thread."""
        self._checker_running = False
        if self._checker_thread and self._checker_thread.is_alive():
            self._checker_thread.join(timeout=2.0)
            self._checker_thread = None


class HubCacheManager:
    """Manages persistent storage of discovered RRC hubs."""

    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.hubs: dict[str, dict] = {}

    def load(self) -> dict[str, dict]:
        """Load discovered hubs from cache file."""
        try:
            if self.cache_path.exists():
                with open(self.cache_path, encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, dict):
                    for hash_hex, hub_info in data.items():
                        if isinstance(hub_info, dict) and all(
                            key in hub_info for key in ["hash", "name", "last_seen"]
                        ):
                            self.hubs[hash_hex] = hub_info

                    logger.info(f"Loaded {len(self.hubs)} discovered hub(s) from cache")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to load discovered hubs: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error loading discovered hubs: {e}")

        return self.hubs

    def save(self, hubs: dict[str, dict]) -> None:
        """Save discovered hubs to cache file."""
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(hubs, f, indent=2)
            logger.debug(f"Saved {len(hubs)} discovered hub(s) to cache")
        except (OSError, TypeError, ValueError) as e:
            logger.error(f"Failed to save discovered hubs: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error saving discovered hubs: {e}")

    def cleanup_old_hubs(
        self, hubs: dict[str, dict], max_age_days: int = 7
    ) -> dict[str, dict]:
        """Remove hubs not seen in max_age_days. Returns cleaned dict."""
        cutoff_time = time.time() - (max_age_days * 24 * 3600)
        cleaned = {
            hash_hex: hub_info
            for hash_hex, hub_info in hubs.items()
            if hub_info.get("last_seen", 0) > cutoff_time
        }
        removed_count = len(hubs) - len(cleaned)
        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} old hub(s) from cache")
        return cleaned


class MessageFormatter:
    """Handles formatting of chat messages with timestamps and styling."""

    def __init__(self, config: dict):
        self.config = config

    def format_timestamp(self) -> str:
        """Format current timestamp."""
        if self.config.get("show_timestamps", True):
            fmt = self.config.get("timestamp_format", "%H:%M:%S")
            return f"[{datetime.now().strftime(fmt)}] "
        return ""

    def format_user_message(
        self, nick: str, text: str, include_timestamp: bool = True
    ) -> str:
        """Format a user message with timestamp and nickname."""
        timestamp = self.format_timestamp() if include_timestamp else ""
        return f"{timestamp}<{nick}> {text}"

    def format_system_message(self, text: str, include_timestamp: bool = True) -> str:
        """Format a system message."""
        timestamp = self.format_timestamp() if include_timestamp else ""
        return f"{timestamp}--- {text}"

    def format_notice(self, text: str, include_timestamp: bool = True) -> str:
        """Format a notice message."""
        timestamp = self.format_timestamp() if include_timestamp else ""
        return f"{timestamp}*** {text}"

    def format_error(self, text: str, include_timestamp: bool = True) -> str:
        """Format an error message."""
        timestamp = self.format_timestamp() if include_timestamp else ""
        return f"{timestamp}!!! ERROR: {text}"

    def format_command(self, text: str, include_timestamp: bool = True) -> str:
        """Format a slash command."""
        timestamp = self.format_timestamp() if include_timestamp else ""
        return f"{timestamp}{text}"


def format_time_ago(timestamp: float) -> str:
    """Format a timestamp as relative time (e.g., '5m ago', '2h ago').

    Args:
        timestamp: Unix timestamp

    Returns:
        Human-readable relative time string
    """
    if timestamp <= 0:
        return "Unknown"

    elapsed = int(time.time() - timestamp)
    if elapsed < 60:
        return "Just now"
    elif elapsed < 3600:
        return f"{elapsed // 60}m ago"
    elif elapsed < 86400:
        return f"{elapsed // 3600}h ago"
    else:
        return f"{elapsed // 86400}d ago"


class HubAnnounceHandler:
    """Handler for RRC hub announcements on the Reticulum network."""

    def __init__(self, app: RRCTextualApp):
        """Initialize the announce handler.

        Args:
            app: Reference to the RRCTextualApp instance
        """
        self.app = app
        self.aspect_filter = "rrc.hub"

    def _extract_hub_name_from_cbor(self, decoded: object) -> str | None:
        """Extract hub name from decoded CBOR data.

        Args:
            decoded: Decoded CBOR object (dict, list, or str)

        Returns:
            Hub name if found, None otherwise
        """
        if isinstance(decoded, dict):
            if decoded.get("proto") == "rrc" and "hub" in decoded:
                return decoded["hub"] if isinstance(decoded["hub"], str) else None

            for key in ["name", "n", "hub"]:
                value = decoded.get(key)
                if isinstance(value, str):
                    return value

        elif (
            isinstance(decoded, list)
            and len(decoded) >= 1
            and isinstance(decoded[-1], str)
        ):
            return decoded[-1]

        elif isinstance(decoded, str) and len(decoded) <= MAX_HUB_NAME_LENGTH:
            return decoded

        return None

    def _parse_hub_announce_data(self, app_data: bytes) -> str | None:
        """Parse hub announce app_data to extract hub name.

        Args:
            app_data: Raw announce application data

        Returns:
            Hub name if successfully parsed, None otherwise
        """
        if not app_data or len(app_data) >= MAX_HUB_ANNOUNCE_DATA_BYTES:
            return None

        try:
            import cbor2

            decoded = cbor2.loads(app_data)
            hub_name = self._extract_hub_name_from_cbor(decoded)
            if hub_name:
                return hub_name
        except Exception as e:
            logger.debug(f"Failed to decode CBOR hub announce data: {e}")

        try:
            hub_name = app_data.decode("utf-8")
            if len(hub_name) <= MAX_HUB_NAME_LENGTH:
                return hub_name
        except Exception as e:
            logger.debug(f"Failed to decode hub announce as UTF-8: {e}")

        return None

    def received_announce(
        self,
        destination_hash: bytes,
        announced_identity: RNS.Identity,
        app_data: bytes,
    ) -> None:
        """Handle received announces from the network."""
        try:
            hash_hex = destination_hash.hex()

            hub_name = self._parse_hub_announce_data(app_data)

            if not hub_name:
                hub_name = f"Hub {hash_hex[:8]}"

            sanitized_hub_name = sanitize_display_name(
                hub_name, max_length=MAX_HUB_NAME_LENGTH, strict=True
            )
            if not sanitized_hub_name:
                sanitized_hub_name = f"Hub {hash_hex[:8]}"

            hub_info = {
                "hash": hash_hex,
                "name": sanitized_hub_name,
                "last_seen": time.time(),
            }

            if hasattr(self.app, "discovered_hubs"):
                self.app.discovered_hubs[hash_hex] = hub_info
                logger.info(
                    f"Discovered RRC hub: {sanitized_hub_name} ({hash_hex[:16]}...)"
                )

                if hasattr(self.app, "_save_discovered_hubs"):
                    self.app._save_discovered_hubs()

                if (
                    hasattr(self.app, "active_discovery_screen")
                    and self.app.active_discovery_screen
                ):
                    self.app.active_discovery_screen.refresh_hub_list()

        except (AttributeError, KeyError, ValueError) as e:
            logger.warning(f"Error processing hub announcement: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error processing hub announcement: {e}")


class JoinRoomScreen(ModalScreen[str | None]):
    """Modal screen for joining a room."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel", show=False),
    ]

    CSS = """
    JoinRoomScreen {
        align: center middle;
        layout: vertical;
    }
    
    #join_dialog {
        width: 50;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
        layout: vertical;
    }
    
    #join_dialog Label {
        width: 100%;
        content-align: center middle;
        height: 1;
    }
    
    #join_dialog Input {
        width: 100%;
        margin: 1 1 2 1;
        height: 1;
    }
    
    #button_bar {
        width: 100%;
        height: 3;
        align: center middle;
    }
    
    #button_bar Button {
        margin: 0 1;
        min-width: 10;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="join_dialog"):
            yield Label("Join Room")
            yield Input(placeholder="Enter room name...", id="room_name_input")
            with Horizontal(id="button_bar"):
                yield Button("Join", variant="primary", id="join_btn")
                yield Button("Cancel", id="cancel_btn")

    def on_mount(self) -> None:
        """Focus the input when mounted."""
        self.query_one("#room_name_input", Input).focus()

    @on(Button.Pressed, "#join_btn")
    def on_join_pressed(self) -> None:
        """Handle join button press."""
        room_name = self.query_one("#room_name_input", Input).value.strip()
        if room_name:
            self.dismiss(room_name)

    @on(Button.Pressed, "#cancel_btn")
    def on_cancel_pressed(self) -> None:
        """Handle cancel button press."""
        self.dismiss(None)

    @on(Input.Submitted, "#room_name_input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle enter key in input field."""
        room_name = event.value.strip()
        if room_name:
            self.dismiss(room_name)

    def action_dismiss_modal(self) -> None:
        """Dismiss the modal with None (cancel)."""
        self.dismiss(None)


class HubDiscoveryScreen(ModalScreen[str | None]):
    """Modal screen for discovering and selecting RRC hubs."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel", show=False),
    ]

    @property
    def app(self) -> RRCTextualApp:  # type: ignore[override]
        """Get the app instance with proper typing."""
        return super().app  # type: ignore[return-value]

    CSS = """
    HubDiscoveryScreen {
        align: center middle;
        layout: vertical;
    }
    
    #discovery_dialog {
        width: 80;
        height: 20;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
        layout: vertical;
    }
    
    #discovery_dialog Label {
        width: 100%;
        content-align: center middle;
        height: 1;
    }
    
    #hub_list {
        width: 100%;
        height: 1fr;
        border: solid $accent;
    }
    
    #discovery_status {
        width: 100%;
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    
    #discovery_button_bar {
        width: 100%;
        height: 3;
        align: center middle;
    }
    
    #discovery_button_bar Button {
        margin: 0 1;
        min-width: 12;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="discovery_dialog"):
            yield Label("Discovered RRC Hubs")
            yield Static("Columns: Name (Hash) - Last Seen", classes="hub-list-header")
            yield ListView(id="hub_list")
            yield Static("Listening for hub announces...", id="discovery_status")
            with Horizontal(id="discovery_button_bar"):
                yield Button(
                    "Connect", variant="primary", id="connect_btn", disabled=True
                )
                yield Button("Refresh", id="refresh_btn")
                yield Button("Cancel", id="cancel_btn")

    def on_mount(self) -> None:
        """Start hub discovery when mounted."""
        self.discovered_hubs: dict[str, str] = {}
        self.selected_hub_hash: str | None = None
        self.app.active_discovery_screen = self
        self._update_hub_list()
        if len(self.app.discovered_hubs) > 0:
            self._update_status(f"Found {len(self.app.discovered_hubs)} hub(s)")
        self._start_discovery()

    def on_unmount(self) -> None:
        """Unregister from auto-updates when closing."""
        if self.app.active_discovery_screen is self:
            self.app.active_discovery_screen = None

    def _start_discovery(self) -> None:
        """Start discovering RRC hubs on the network."""

        def discover():
            try:
                self.app.call_from_thread(
                    self._update_status, "Listening for hub announces..."
                )
                import time

                time.sleep(0.5)

                self.discovered_hubs = {}
                for hub_hash, hub_data in self.app.discovered_hubs.items():
                    self.discovered_hubs[hub_hash] = hub_data.get(
                        "name", f"Hub {hub_hash[:16]}..."
                    )

                self.app.call_from_thread(self._update_hub_list)

                if self.discovered_hubs:
                    self.app.call_from_thread(
                        self._update_status, f"Found {len(self.discovered_hubs)} hub(s)"
                    )
                else:
                    self.app.call_from_thread(
                        self._update_status, "Listening for hub announces..."
                    )

            except Exception as e:
                logger.error(f"Hub discovery error: {e}")
                self.app.call_from_thread(self._update_status, f"Error: {e}")

        thread = threading.Thread(target=discover, daemon=True)
        thread.start()

    def refresh_hub_list(self) -> None:
        """Refresh the hub list (can be called from announce handler thread)."""
        self.app.call_from_thread(self._update_hub_list)
        if len(self.app.discovered_hubs) > 0:
            self.app.call_from_thread(
                self._update_status, f"Found {len(self.app.discovered_hubs)} hub(s)"
            )

    def _update_hub_list(self) -> None:
        """Update the hub list display."""
        hub_list = self.query_one("#hub_list", ListView)
        hub_list.clear()

        sorted_hubs = sorted(
            self.app.discovered_hubs.items(),
            key=lambda x: x[1].get("last_seen", 0),
            reverse=True,
        )

        for hub_hash, hub_info in sorted_hubs:
            hub_name = hub_info.get("name", f"Hub {hub_hash[:16]}...")
            last_seen = hub_info.get("last_seen", 0)
            time_str = format_time_ago(last_seen)

            text_obj = Text(
                f"{hub_name} ({hub_hash[:16]}...) - {time_str}",
                no_wrap=True,
                overflow="ellipsis",
            )
            item = ListItem(Static(text_obj))
            item.hub_hash = hub_hash  # type: ignore[attr-defined]
            self.discovered_hubs[hub_hash] = hub_name
            hub_list.append(item)

    def _update_status(self, status: str) -> None:
        """Update the status message."""
        self.query_one("#discovery_status", Static).update(status)

    @on(ListView.Selected, "#hub_list")
    def on_hub_selected(self, event: ListView.Selected) -> None:
        """Handle hub selection."""
        if hasattr(event.item, "hub_hash"):
            self.selected_hub_hash = event.item.hub_hash
            self.query_one("#connect_btn", Button).disabled = False

    @on(Button.Pressed, "#connect_btn")
    def on_connect_pressed(self) -> None:
        """Handle connect button press."""
        if self.selected_hub_hash:
            self.dismiss(self.selected_hub_hash)

    @on(Button.Pressed, "#refresh_btn")
    def on_refresh_pressed(self) -> None:
        """Handle refresh button press."""
        self.discovered_hubs.clear()
        self.selected_hub_hash = None
        self.query_one("#connect_btn", Button).disabled = True
        self._update_hub_list()
        self._start_discovery()

    @on(Button.Pressed, "#cancel_btn")
    def on_cancel_pressed(self) -> None:
        """Handle cancel button press."""
        self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        """Dismiss the modal with None (cancel)."""
        self.dismiss(None)


class MessageLine(Static):
    """A single message line with styling."""

    def __init__(self, text: str, style: str = "default", **kwargs):
        super().__init__(text, **kwargs)
        self.message_style = style
        self.add_class(style)


class RoomButton(ListItem):
    """A room button in the room list."""

    def __init__(self, room_name: str, unread_count: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.room_name = room_name
        self.unread_count = unread_count
        display_text = room_name
        if unread_count > 0:
            display_text = f"{room_name} ({unread_count})"
        self.text_obj = Text(display_text, no_wrap=True, overflow="ellipsis")

    def compose(self) -> ComposeResult:
        yield Static(self.text_obj)


class RRCTextualApp(App):
    """Textual-based TUI for RRC client."""

    MIN_WIDTH = 160
    MIN_HEIGHT = 30

    CSS = """
    Screen {
        layout: grid;
        grid-size: 3 1;
        grid-columns: 20 1fr 25;
    }
    
    Screen.hide-users {
        grid-size: 2 1;
        grid-columns: 20 1fr;
    }
    
    #room_container {
        border: solid $primary;
        height: 100%;
        padding: 0 1;
    }
    
    #message_container {
        border: solid $primary;
        height: 100%;
        layout: vertical;
    }
    
    #room_info {
        width: 100%;
        height: 1;
        background: $accent;
        color: $text;
        padding: 0 1;
    }
    
    #user_container {
        border: solid $primary;
        height: 100%;
        padding: 0 0;
    }
    
    #user_container.hidden {
        display: none;
    }
    
    #message_display {
        height: 1fr;
        border: solid $accent;
        background: $surface;
    }
    
    RichLog {
        background: $surface;
    }
    
    #input_box {
        height: 3;
        border: solid $accent;
        layout: horizontal;
        padding: 0 1;
    }
    
    #input_prompt {
        width: auto;
    }
    
    #input_field {
        width: 1fr;
    }
    
    Input {
        background: $surface;
        border: none;
    }
    
    ListView {
        height: 100%;
    }
    
    ListItem {
        height: auto;
        padding: 0 1;
    }
    
    .own_msg_pending {
        color: $warning;
    }
    
    .own_msg_confirmed {
        color: $success;
    }
    
    .own_msg_failed {
        color: $error;
    }
    
    .command {
        color: magenta;
    }
    
    .notice {
        color: cyan;
    }
    
    .error {
        color: $error;
    }
    
    .system {
        color: $success;
    }
    
    .room_active {
        background: $accent;
        color: $text;
    }
    """

    BINDINGS = [
        Binding("f1", "show_help", "Help"),
        Binding("f2", "join_room", "Join"),
        Binding("f3", "part_room", "Part"),
        Binding("f4", "connect", "Connect"),
        Binding("f5", "disconnect", "Disconnect"),
        Binding("f6", "discover_hubs", "Discover"),
        Binding("f10", "quit", "Quit"),
    ]

    HUB_ROOM = "[Hub]"

    link_active = reactive(False)
    latency_ms: reactive[float | None] = reactive(None, init=False)

    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.client: Client | None = None
        self.active_room: str = self.HUB_ROOM
        self.hub_name: str | None = None
        self.nickname_map: dict[str, str] = {}
        self.own_identity_hash: str | None = None

        self.rooms: dict[str, RoomState] = {self.HUB_ROOM: RoomState()}

        self.discovered_hubs: dict[str, dict] = {}
        self.active_discovery_screen = None
        self.hub_cache_path = Path.home() / ".rrc-tui" / "discovered_hubs.json"
        self.hub_cache_manager = HubCacheManager(self.hub_cache_path)

        self.message_formatter = MessageFormatter(self.config)

        self.input_history: list[str] = []
        self.input_history_index: int = -1
        self.input_buffer: str = ""

        self.pending_tracker = PendingMessageTracker(
            timeout_seconds=self.config.get("message_timeout_seconds", 30.0)
        )

        try:
            configdir = self.config.get("configdir") or None
            if RNS.Reticulum.get_instance() is None:
                RNS.Reticulum(configdir=configdir)
        except Exception as e:
            logger.error(f"Failed to initialize Reticulum: {e}")

        self.announce_handler = HubAnnounceHandler(self)
        RNS.Transport.register_announce_handler(self.announce_handler)

        self._load_discovered_hubs()

    def _load_discovered_hubs(self) -> None:
        """Load discovered hubs from cache file."""
        self.discovered_hubs = self.hub_cache_manager.load()
        max_age = self.config.get("hub_cache_max_age_days", 7)
        self.discovered_hubs = self.hub_cache_manager.cleanup_old_hubs(
            self.discovered_hubs, max_age
        )

    def _save_discovered_hubs(self) -> None:
        """Save discovered hubs to cache file."""
        self.hub_cache_manager.save(self.discovered_hubs)

    def compose(self) -> ComposeResult:
        """Compose the UI layout."""
        yield Header()

        with Container(id="room_container"):
            yield Static("Rooms", classes="box-title")
            yield ListView(id="room_list")

        with Container(id="message_container"):
            yield Static("", id="room_info")
            yield RichLog(id="message_display", highlight=False, markup=True, wrap=True)
            with Container(id="input_box"):
                yield Static("> ", id="input_prompt")
                yield Input(placeholder="Type a message...", id="input_field")

        with Container(id="user_container"):
            yield Static("Users", classes="box-title")
            yield ListView(id="user_list")

        yield Footer()

    def on_mount(self) -> None:
        """Initialize the app when mounted."""
        self.title = "RRC TUI"

        self.screen.add_class("hide-users")
        self.query_one("#user_container").add_class("hidden")

        self._update_room_list()
        self._update_room_info()
        self._add_message(self.HUB_ROOM, "system", "Welcome to RRC TUI")
        self._add_message(self.HUB_ROOM, "system", "Press F1 for help, F4 to connect")

        self.set_timer(0.1, self._focus_input)

        self.set_interval(1.0, self._update_link_status)

    def _safe_call_from_thread(
        self, func: Callable[..., None], *args: object, **kwargs: object
    ) -> None:
        """Safely call a function from any thread, handling both main and background threads.

        This wrapper handles the case where callbacks might be called from the main thread
        (e.g., user-initiated disconnect) or from a background thread (e.g., network events).
        """
        try:
            self.call_from_thread(func, *args, **kwargs)
        except RuntimeError as e:
            if "must run in a different thread" in str(e):
                func(*args, **kwargs)
            else:
                raise

    def _update_link_status(self) -> None:
        """Check and update link status."""
        if self.client and self.client.link:
            self.link_active = self.client.link.status == RNS.Link.ACTIVE
            if self.client.latency_ms is not None:
                self.latency_ms = self.client.latency_ms
        else:
            self.link_active = False
            self.latency_ms = None

    def watch_link_active(self, active: bool) -> None:
        """React to link status changes."""
        self._update_header()

    def watch_latency_ms(self, latency: float | None) -> None:
        """React to latency changes."""
        self._update_header()

    def _update_header(self) -> None:
        """Update the header with current link status and latency."""
        header = self.query_one(Header)
        if self.link_active:
            status = f"Link: {self.hub_name} ✓ Active"
            if self.latency_ms is not None:
                status += f" | Latency: {self.latency_ms:.0f}ms"
            header.screen.sub_title = status
        else:
            header.screen.sub_title = "Link: ✗ Inactive"

    def _update_room_list(self) -> None:
        """Update the room list display."""
        room_list = self.query_one("#room_list", ListView)
        room_list.clear()

        rooms = [self.HUB_ROOM] + sorted(
            [r for r in self.rooms.keys() if r != self.HUB_ROOM]
        )

        for room in rooms:
            room_state = self.rooms.get(room, RoomState())
            item = RoomButton(room, room_state.unread_count)
            if room == self.active_room:
                item.add_class("room_active")
            room_list.append(item)

    def _update_user_list(self) -> None:
        """Update the user list for the active room."""
        user_list = self.query_one("#user_list", ListView)
        user_list.clear()

        user_container = self.query_one("#user_container")
        screen = self.screen

        if self.active_room == self.HUB_ROOM:
            user_container.add_class("hidden")
            screen.add_class("hide-users")
        else:
            user_container.remove_class("hidden")
            screen.remove_class("hide-users")

        if self.active_room in self.rooms:
            room_state = self.rooms[self.active_room]
            users = sorted(room_state.users)
            for user_hash in users:
                nick = self.nickname_map.get(user_hash, format_identity_hash(user_hash))
                text_obj = Text(f"  {nick}", no_wrap=True, overflow="ellipsis")
                user_list.append(ListItem(Static(text_obj)))

    def _update_room_info(self) -> None:
        """Update the room info header."""
        room_info = self.query_one("#room_info", Static)

        info_parts = [self.active_room]

        if self.active_room in self.rooms:
            room_state = self.rooms[self.active_room]
            if room_state.mode:
                info_parts.append(f"[{room_state.mode}]")
            if room_state.topic:
                info_parts.append(f"- {room_state.topic}")

        room_info.update(" ".join(info_parts))

    def _update_message_display(self) -> None:
        """Update the message display for the active room."""
        message_log = self.query_one("#message_display", RichLog)
        message_log.clear()

        if self.active_room in self.rooms:
            room_state = self.rooms[self.active_room]
            for style, text in room_state.messages:
                rich_text = self._style_message_text(text, style)
                message_log.write(rich_text)

    def _style_message_text(self, text: str, style: str) -> Text:
        """Apply styling to message text.

        Args:
            text: Message text
            style: Style name

        Returns:
            Styled Text object
        """
        rich_text = Text(text)
        if style == "own_msg_pending":
            rich_text.stylize("yellow")
        elif style == "own_msg_confirmed":
            rich_text.stylize("green")
        elif style == "own_msg_failed":
            rich_text.stylize("red")
        elif style == "command":
            rich_text.stylize("magenta")
        elif style == "notice":
            rich_text.stylize("cyan")
        elif style == "error":
            rich_text.stylize("red bold")
        elif style == "system":
            rich_text.stylize("green")

        return rich_text

    def _append_message_to_display(self, style: str, text: str) -> None:
        """Append a single message to the display without full rebuild.

        Args:
            style: Message style
            text: Message text
        """
        if self.active_room not in self.rooms:
            return

        message_log = self.query_one("#message_display", RichLog)
        rich_text = self._style_message_text(text, style)
        message_log.write(rich_text)

    def _switch_room(self, room: str) -> None:
        """Switch to a different room."""
        if self.active_room == room:
            return

        self.active_room = room
        self.title = "RRC TUI"

        if room in self.rooms:
            self.rooms[room].unread_count = 0

        self._update_room_list()
        self._update_room_info()
        self._update_user_list()
        self._update_message_display()
        try:
            self.query_one("#input_field", Input).focus()
        except Exception as e:
            logger.debug(f"Could not focus input field: {e}")

    def _focus_input(self) -> None:
        """Helper method to focus the input field."""
        try:
            self.query_one("#input_field", Input).focus()
        except Exception as e:
            logger.debug(f"Could not focus input field: {e}")

    def _add_message(self, room: str, style: str, message: str) -> None:
        """Add a message to a room."""
        if room not in self.rooms:
            self.rooms[room] = RoomState()

        room_state = self.rooms[room]
        room_state.messages.append((style, message))

        max_messages = self.config.get(
            "max_messages_per_room", MAX_MESSAGES_PER_ROOM_DEFAULT
        )
        if len(room_state.messages) > max_messages:
            room_state.messages = room_state.messages[-max_messages:]

        if room == self.active_room:
            self._append_message_to_display(style, message)
        else:
            if style not in ["system", "notice", "error"]:
                room_state.unread_count += 1
                self._update_room_list()

    def _cleanup_room_data(self, room: str) -> None:
        """Clean up all data for a room (consolidates duplicate cleanup logic)."""
        if room in self.rooms:
            del self.rooms[room]

    def _format_timestamp(self) -> str:
        """Format current timestamp."""
        return self.message_formatter.format_timestamp()

    def _show_notice(self, message: str, room: str | None = None) -> None:
        """Show a notice message."""
        target_room = room if room else self.HUB_ROOM
        formatted = self.message_formatter.format_notice(message)
        self._add_message(target_room, "notice", formatted)

    def _show_error(self, message: str, room: str | None = None) -> None:
        """Show an error message."""
        target_room = room if room else self.HUB_ROOM
        formatted = self.message_formatter.format_error(message)
        self._add_message(target_room, "error", formatted)

    def _show_system(self, message: str, room: str | None = None) -> None:
        """Show a system message."""
        target_room = room if room else self.HUB_ROOM
        formatted = self.message_formatter.format_system_message(message)
        self._add_message(target_room, "system", formatted)

    def _parse_room_info_from_notice(
        self, message: str, room: str | None = None
    ) -> None:
        """Parse room info updates from hub notices."""
        import re

        match = re.match(r"room (\S+):[^;]*;\s*mode=([^;]+);\s*topic=(.+)", message)
        if match:
            room_name = normalize_room_name(match.group(1))
            mode = match.group(2).strip()
            topic = match.group(3).strip()

            if room_name not in self.rooms:
                self.rooms[room_name] = RoomState()
            self.rooms[room_name].mode = mode
            self.rooms[room_name].topic = topic
            self._update_room_info()
            return

        match = re.match(r"mode for (\S+) is now:\s*(.+)", message)
        if match:
            room_name = normalize_room_name(match.group(1))
            modes = match.group(2).strip()

            if room_name not in self.rooms:
                self.rooms[room_name] = RoomState()
            self.rooms[room_name].mode = modes
            self._update_room_info()
            return

        match = re.match(r"topic for (\S+) is now:\s*(.+)", message)
        if match:
            room_name = normalize_room_name(match.group(1))
            topic = match.group(2).strip()

            if room_name not in self.rooms:
                self.rooms[room_name] = RoomState()
            self.rooms[room_name].topic = topic
            self._update_room_info()
            return

    @on(ListView.Selected, "#room_list")
    def on_room_selected(self, event: ListView.Selected) -> None:
        """Handle room selection."""
        if isinstance(event.item, RoomButton):
            self._switch_room(event.item.room_name)

    @on(Input.Submitted, "#input_field")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle message submission."""
        self._send_message(event.value)
        event.input.value = ""

    def _send_message(self, text: str) -> None:
        """Send a message to the active room."""
        if not text.strip():
            return

        if self.client is None:
            self._show_error("Not connected.")
            return

        text = text.strip()

        if self.config.get("save_input_history", True):
            self.input_history.append(text)
            max_history = self.config.get("input_history_size", 50)
            if len(self.input_history) > max_history:
                self.input_history = self.input_history[-max_history:]

        self.input_history_index = -1

        is_command = text.startswith("/")

        if is_command:
            handled = self._handle_slash_command(text)
            if handled:
                return
        else:
            if self.active_room == self.HUB_ROOM:
                self._show_error(
                    "Cannot send messages to Hub room. Use slash commands like /who or /list."
                )
                return

        try:
            msg_id = self.client.msg(self.active_room, text)

            if is_command:
                formatted = self.message_formatter.format_command(text)
                self._add_message(self.active_room, "command", formatted)
            else:
                nick = self.config.get("nickname") or format_identity_hash(self.own_identity_hash or "")
                formatted = self.message_formatter.format_user_message(nick, text)
                self._add_message(self.active_room, "own_msg_pending", formatted)

                self.pending_tracker.add(msg_id, self.active_room, text)

                self.pending_tracker.start_checker(self._handle_message_timeout)

        except Exception as e:
            self._show_error(f"Failed to send message: {e}")

    def _handle_slash_command(self, text: str) -> bool:
        """Handle slash commands. Returns True if handled client-side."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ["/help", "/h", "/?"]:
            self._show_help_message()
            return True

        elif cmd == "/nick":
            if not arg:
                self._show_error("Usage: /nick <nickname>")
                return True
            new_nick = sanitize_display_name(arg)
            self.config["nickname"] = new_nick
            if self.client:
                self.client.nickname = new_nick
            self._show_system(f"Nickname changed to: {new_nick}")
            return True

        elif cmd == "/join":
            if not arg:
                self._show_error("Usage: /join <room>")
                return True
            self._join_room_by_name(normalize_room_name(arg))
            return True

        elif cmd in ["/part", "/leave"]:
            room = normalize_room_name(arg) if arg else self.active_room
            self._part_room_by_name(room)
            return True

        elif cmd == "/clear":
            if self.active_room in self.rooms:
                self.rooms[self.active_room].messages = []
                self._update_message_display()
            return True

        elif cmd in ["/quit", "/exit"]:
            self.exit()
            return True

        return False

    def _show_help_message(self) -> None:
        """Show help information."""
        help_text = [
            "",
            "=== RRC TUI Help ===",
            "",
            "Keyboard Shortcuts:",
            "F1: Show this help",
            "F2: Join a room",
            "F3: Part from active room",
            "F4: Connect to hub",
            "F5: Disconnect from hub",
            "F6: Discover RRC hubs on network",
            "F10: Quit application",
            "",
            "Message Status Colors:",
            "Yellow - Sent, awaiting confirmation",
            "Green - Confirmed by hub",
            "Red - Timeout/delivery failed",
            "Magenta - Slash command (no echo)",
            "",
            "Slash Commands:",
            "/help - Show this help message",
            "/nick <nickname> - Change your nickname",
            "/join <room> - Join a room",
            "/part [room] - Part from a room (defaults to active room)",
            "/clear - Clear message display for active room",
            "/quit - Quit the application",
            "",
        ]
        for line in help_text:
            self._add_message(self.HUB_ROOM, "system", line)

    def _join_room_by_name(self, room: str) -> None:
        """Join a room by name."""
        if self.client is None:
            self._show_error("Not connected.")
            return

        try:
            self.client.join(room)
            self._show_system(f"Joining room: {room}")
        except Exception as e:
            self._show_error(f"Failed to join room: {e}")

    def _part_room_by_name(self, room: str) -> None:
        """Part from a room by name."""
        if self.client is None:
            self._show_error("Not connected.")
            return

        if room == self.HUB_ROOM:
            self._show_error("Cannot part from Hub room.")
            return

        try:
            self.client.part(room)
            self._show_system(f"Parting from room: {room}", self.HUB_ROOM)

            if self.active_room == room:
                self.active_room = self.HUB_ROOM

            self._cleanup_room_data(room)

            self._update_room_list()
            self._update_room_info()
            self._update_user_list()
            self._update_message_display()

        except Exception as e:
            self._show_error(f"Failed to part from room: {e}")

    def _handle_message_timeout(self, msg_id: bytes, room: str, text: str) -> None:
        """Handle a message timeout (called by PendingMessageTracker)."""
        if room in self.rooms:
            room_state = self.rooms[room]
            nick = self.config.get("nickname", "You")
            old_msg = self.message_formatter.format_user_message(nick, text)
            new_msg = f"{old_msg} [TIMEOUT - message may not have been received]"

            for i, (style, msg) in enumerate(room_state.messages):
                if style == "own_msg_pending" and text in msg:
                    room_state.messages[i] = ("own_msg_failed", new_msg)
                    break

            if room == self.active_room:
                self.call_from_thread(self._update_message_display)

    def _handle_rrc_welcome(self, env: dict) -> None:
        """Handle WELCOME message."""
        body = env.get(K_BODY, {})
        self.hub_name = body.get(B_WELCOME_HUB, "Unknown Hub")

        self._show_system(f"Connected to {self.hub_name}")

        if isinstance(body, dict) and B_WELCOME_LIMITS in body:
            limits = body[B_WELCOME_LIMITS]
            if isinstance(limits, dict):
                limit_parts = []
                if L_MAX_NICK_BYTES in limits:
                    limit_parts.append(f"nick: {limits[L_MAX_NICK_BYTES]}B")
                if L_MAX_ROOM_NAME_BYTES in limits:
                    limit_parts.append(f"room: {limits[L_MAX_ROOM_NAME_BYTES]}B")
                if L_MAX_MSG_BODY_BYTES in limits:
                    limit_parts.append(f"msg: {limits[L_MAX_MSG_BODY_BYTES]}B")
                if L_MAX_ROOMS_PER_SESSION in limits:
                    limit_parts.append(f"rooms: {limits[L_MAX_ROOMS_PER_SESSION]}")
                if L_RATE_LIMIT_MSGS_PER_MINUTE in limits:
                    limit_parts.append(f"rate: {limits[L_RATE_LIMIT_MSGS_PER_MINUTE]}/min")
                
                if limit_parts:
                    self._show_system(f"Hub limits: {', '.join(limit_parts)}")

        self.title = "RRC TUI"

        if self.client:
            ping_interval = self.config.get("ping_interval_seconds", 5.0)
            if ping_interval > 0:
                self.client.start_ping_thread(interval=ping_interval)

        auto_join = self.config.get("auto_join_room", "").strip()
        if auto_join and self.client:
            try:
                self.client.join(normalize_room_name(auto_join))
            except Exception as e:
                logger.error(f"Failed to auto-join room: {e}")

    def _handle_rrc_message(self, env: dict) -> None:
        """Handle incoming message."""
        room = env.get(K_ROOM)
        if not room:
            return

        room = normalize_room_name(room)
        src = env.get(K_SRC)
        nick = env.get(K_NICK)
        body = env.get(K_BODY, "")
        msg_id = env.get(K_ID)

        if src and nick:
            src_hex = src.hex() if isinstance(src, bytes) else src
            old_nick = self.nickname_map.get(src_hex)
            new_nick = sanitize_display_name(nick)

            self.nickname_map[src_hex] = new_nick

            if old_nick != new_nick and room in self.rooms:
                if src_hex in self.rooms[room].users:
                    self.call_from_thread(self._update_user_list)

        src_hex = src.hex() if isinstance(src, bytes) else src
        if src_hex == self.own_identity_hash and msg_id:
            pending_data = self.pending_tracker.confirm(msg_id)
            if pending_data:
                pending_room, pending_text, sent_time = pending_data

                if pending_room == room and pending_text == body:
                    if room in self.rooms:
                        room_state = self.rooms[room]
                        nick = self.config.get("nickname") or format_identity_hash(self.own_identity_hash or "")
                        old_msg = self.message_formatter.format_user_message(nick, body)

                        for i, (style, msg) in enumerate(room_state.messages):
                            if style == "own_msg_pending" and pending_text in msg:
                                room_state.messages[i] = ("own_msg_confirmed", old_msg)
                                break

                        if self.active_room == room:
                            self.call_from_thread(self._update_message_display)

                    return

        if src_hex and src_hex != self.own_identity_hash:
            display_name = self.nickname_map.get(
                src_hex, format_identity_hash(src_hex)
            )
            formatted = self.message_formatter.format_user_message(display_name, body)
            self.call_from_thread(self._add_message, room, "default", formatted)

    def _handle_rrc_notice(self, env: dict) -> None:
        """Handle NOTICE message."""
        room = env.get(K_ROOM)
        body = env.get(K_BODY, "")

        self._parse_room_info_from_notice(body, room)

        self.call_from_thread(self._show_notice, body, room)

    def _handle_rrc_error(self, env: dict) -> None:
        """Handle ERROR message."""
        body = env.get(K_BODY, "Unknown error")
        self.call_from_thread(self._show_error, body)

    def _handle_rrc_joined(self, room: str, env: dict) -> None:
        """Handle JOINED message."""
        room = normalize_room_name(room)

        if room not in self.rooms:
            self.rooms[room] = RoomState()

        body = env.get(K_BODY, [])
        users = body if isinstance(body, list) else []

        room_state = self.rooms[room]
        already_in_room = (
            self.own_identity_hash in room_state.users
            if self.own_identity_hash
            else False
        )

        if already_in_room:
            if len(users) > 0:
                joining_hash = users[0]
                if isinstance(joining_hash, bytes):
                    joining_hash_hex = joining_hash.hex()
                    room_state.users.add(joining_hash_hex)
                    user_nick = self.nickname_map.get(
                        joining_hash_hex, format_identity_hash(joining_hash_hex)
                    )
                    self.call_from_thread(
                        self._show_system, f"{user_nick} joined room: {room}", room
                    )
                    if self.active_room == room:
                        self.call_from_thread(self._update_user_list)
        else:
            for user_hash in users:
                if isinstance(user_hash, bytes):
                    room_state.users.add(user_hash.hex())

            if self.own_identity_hash:
                room_state.users.add(self.own_identity_hash)

            self.call_from_thread(self._show_system, f"You joined room: {room}", room)
            self.call_from_thread(self._update_room_list)
            self.call_from_thread(self._switch_room, room)

    def _handle_rrc_parted(self, room: str, env: dict) -> None:
        """Handle PARTED message."""
        room = normalize_room_name(room)

        body = env.get(K_BODY, [])
        users = body if isinstance(body, list) else []

        if len(users) == 1:
            parting_hash = users[0]
            if isinstance(parting_hash, bytes):
                parting_hash_hex = parting_hash.hex()

                if parting_hash_hex == self.own_identity_hash:
                    self.call_from_thread(
                        self._show_system,
                        f"You parted from room: {room}",
                        self.HUB_ROOM,
                    )

                    if self.active_room == room:
                        self.active_room = self.HUB_ROOM

                    self._cleanup_room_data(room)

                    self.call_from_thread(self._update_room_list)
                    self.call_from_thread(self._update_room_info)
                    self.call_from_thread(self._update_user_list)
                    self.call_from_thread(self._update_message_display)
                else:
                    user_nick = self.nickname_map.get(
                        parting_hash_hex, format_identity_hash(parting_hash_hex)
                    )
                    self.call_from_thread(
                        self._show_system, f"{user_nick} parted from room: {room}", room
                    )
                    if room in self.rooms:
                        self.rooms[room].users.discard(parting_hash_hex)
                        if self.active_room == room:
                            self.call_from_thread(self._update_user_list)

    def _handle_rrc_pong(self, env: dict) -> None:
        """Handle PONG response and calculate latency."""
        if self.client and self.client._last_ping_time is not None:
            import time

            latency = (time.monotonic() - self.client._last_ping_time) * 1000
            self.client.latency_ms = latency
            self._safe_call_from_thread(self._update_latency, latency)

    def _update_latency(self, latency: float) -> None:
        """Update the latency reactive property."""
        self.latency_ms = latency

    def _handle_rrc_close(self) -> None:
        """Handle connection close."""
        self._safe_call_from_thread(self._show_system, "Connection closed")
        self.title = "RRC TUI"
        self._safe_call_from_thread(self._cleanup_after_disconnect)

    def _cleanup_after_disconnect(self) -> None:
        """Clean up after disconnect."""
        self.client = None
        self.nickname_map.clear()
        self.pending_tracker.clear()
        self.pending_tracker.stop_checker()
        self.latency_ms = None

        rooms_to_remove = [r for r in self.rooms.keys() if r != self.HUB_ROOM]
        for room in rooms_to_remove:
            del self.rooms[room]

        self.active_room = self.HUB_ROOM
        self._update_room_list()
        self._update_user_list()
        self._update_message_display()

    def action_show_help(self) -> None:
        """Show help dialog."""
        self._show_help_message()
        self._focus_input()

    def action_join_room(self) -> None:
        """Prompt to join a room."""

        def handle_room_name(room_name: str | None) -> None:
            if room_name:
                self._join_room_by_name(normalize_room_name(room_name))
            self._focus_input()

        self.push_screen(JoinRoomScreen(), handle_room_name)

    def action_part_room(self) -> None:
        """Part from the active room."""
        if self.active_room == self.HUB_ROOM:
            self._show_error("Cannot part from Hub room.")
        else:
            self._part_room_by_name(self.active_room)
        self._focus_input()

    def action_discover_hubs(self) -> None:
        """Show hub discovery dialog."""

        def handle_hub_selected(hub_hash: str | None) -> None:
            if hub_hash:
                self.config["hub_hash"] = hub_hash
                self._show_system(f"Selected hub: {hub_hash[:16]}...")
                self._show_system("Press F4 to connect")
            self._focus_input()

        self.push_screen(HubDiscoveryScreen(), handle_hub_selected)

    def action_connect(self) -> None:
        """Connect to RRC hub."""
        if self.client is not None:
            self._show_error("Already connected. Disconnect first.")
            return

        hub_hash = self.config.get("hub_hash", "").strip()
        if not hub_hash:
            self._show_error(
                "No hub configured. Press F6 to discover hubs or set hub_hash in config."
            )
            return

        nickname = self.config.get("nickname", "").strip() or None

        try:
            hub_dest_hash = parse_hash(hub_hash)
        except ValueError as e:
            self._show_error(f"Invalid hub hash: {e}")
            return

        identity_path = self.config.get("identity_path", "~/.rrc-tui/identity")
        identity = load_or_create_identity(identity_path)
        self.own_identity_hash = identity.hash.hex()

        self._show_system(f"Connecting to hub {hub_hash[:16]}...")
        self.title = "RRC TUI - Connecting..."

        def connect_thread():
            try:
                client_config = ClientConfig(
                    dest_name=self.config.get("dest_name", "rrc.hub")
                )
                self.client = Client(identity, client_config, nickname=nickname)

                self.client.on_welcome = self._handle_rrc_welcome
                self.client.on_message = self._handle_rrc_message
                self.client.on_notice = self._handle_rrc_notice
                self.client.on_error = self._handle_rrc_error
                self.client.on_joined = self._handle_rrc_joined
                self.client.on_parted = self._handle_rrc_parted
                self.client.on_close = self._handle_rrc_close
                self.client.on_pong = self._handle_rrc_pong

                timeout = self.config.get("connection_timeout_seconds", 30)
                self.client.connect(hub_dest_hash, timeout_s=timeout)

            except TimeoutError as e:
                logger.error(f"Connection timeout: {e}")
                self.call_from_thread(self._show_error, f"Connection timeout: {e}")
                self.client = None
                self.call_from_thread(lambda: setattr(self, "title", "RRC TUI"))
            except (ValueError, RuntimeError) as e:
                logger.error(f"Connection failed: {e}")
                self.call_from_thread(self._show_error, f"Connection failed: {e}")
                self.client = None
                self.call_from_thread(lambda: setattr(self, "title", "RRC TUI"))
            except Exception as e:
                logger.exception(f"Unexpected connection error: {e}")
                self.call_from_thread(self._show_error, f"Connection failed: {e}")
                self.client = None
                self.call_from_thread(lambda: setattr(self, "title", "RRC TUI"))

        thread = threading.Thread(target=connect_thread, daemon=True)
        thread.start()

    def action_disconnect(self) -> None:
        """Disconnect from RRC hub."""
        if self.client is None:
            self._show_error("Not connected.")
            return

        self._show_system("Disconnecting...")

        try:
            self.client.close()
        except Exception as e:
            logger.error(f"Error during disconnect: {e}")

        self._cleanup_after_disconnect()

    async def action_quit(self) -> None:
        """Quit the application, properly closing any active connection first."""
        if self.client is not None:
            try:
                self.client.close()
            except Exception as e:
                logger.debug(f"Error closing client during quit: {e}")

        self.exit()


def run_textual_tui():
    """Entry point for running the Textual TUI."""
    app = RRCTextualApp()
    app.run()
