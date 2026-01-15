# RRC-TUI Configuration

## Configuration Directory Search

RRC-TUI automatically searches for existing configuration and identity files in the following locations (in order):

1. `/etc/rrc-tui/` - System-wide configuration
2. `~/.config/rrc-tui/` - XDG Base Directory standard location
3. `~/.rrc-tui/` - Legacy/default location

The first existing directory found will be used. If no directory exists, `~/.rrc-tui/` will be created with default configuration and a new identity on first run.

## Sample Configuration

This is an example configuration file for RRC-TUI. The configuration file will be created at `<config-dir>/config.json` and identity at `<config-dir>/identity`.

```json
{
  "hub_hash": "",
  "nickname": "YourNickname",
  "auto_join_room": "general",
  "identity_path": "~/.rrc-tui/identity",
  "dest_name": "rrc.hub",
  "configdir": "",
  "log_level": "INFO",
  "log_to_file": true,
  "log_to_console": false,
  "max_log_size_mb": 10,
  "log_backup_count": 5,
  "rate_limit_enabled": true,
  "rate_warning_threshold": 0.8,
  "input_history_size": 50,
  "save_input_history": true,
  "max_messages_per_room": 500,
  "show_timestamps": true,
  "timestamp_format": "%H:%M:%S",
  "auto_reconnect": true,
  "reconnect_delay_seconds": 5,
  "connection_timeout_seconds": 30,
  "ping_interval_seconds": 5.0
}
```

## Configuration Options

- **hub_hash**: The hash of the RRC hub to connect to (optional - use F6 to discover hubs)
- **nickname**: Your display name in chat (optional - defaults to your identity hash if not set)
- **auto_join_room**: Room to automatically join after connecting (optional)
- **identity_path**: Path to your Reticulum identity file
- **dest_name**: Reticulum destination name (default: "rrc.hub")
- **configdir**: Reticulum config directory (optional)
- **log_level**: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- **log_to_file**: Enable file logging
- **max_messages_per_room**: Maximum messages to keep per room
- **show_timestamps**: Show timestamps on messages
- **timestamp_format**: Python strftime format for timestamps
- **connection_timeout_seconds**: Timeout for initial connection
- **ping_interval_seconds**: Interval between ping messages to measure latency (default: 5.0, set to 0 to disable)

## Usage

1. Run `rrc-tui` from the command line
2. Press F6 to discover available hubs (or set `hub_hash` in config manually)
3. Press F4 to connect to the hub
4. Press F2 to join a room
5. Type your message and press Enter to send (optionally set `nickname` in config)
6. Press F10 to quit

## Keyboard Shortcuts

- **F1**: Show help
- **F2**: Join a room
- **F3**: Part from active room
- **F4**: Connect to hub
- **F5**: Disconnect from hub
- **F10**: Quit application
- **Enter**: Send message
- **Up/Down**: Navigate input history
