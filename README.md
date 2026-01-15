# RRC-TUI

A Text User Interface (TUI) client for RRC (Reticulum Relay Chat).

![RRC-TUI Screenshot](/tui.png)

## Installation

### Prerequisites

- Python 3.11 or higher
- Reticulum network access

### Install from source

```bash
# Clone the repository
git clone https://github.com/kc1awv/rrc-tui.git
cd rrc-tui

# Create and activate a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate

# Install to the virtual environment
pip install -e .

# Install with development dependencies
pip install -e ."[dev]"
```

## Configuration

Configuration is optional - RRC-TUI will create a default configuration on first
run. 

1. The configuration file will be created at `~/.rrc-tui/config.json` on first
    run
2. Optionally set your desired hub hash and nickname, and room to auto-join, in
    the config file:

```json
{
  "hub_hash": "your_hub_hash_here",
  "nickname": "YourNickname",
  "auto_join_room": "general"
}
```

`hub_hash`, `nickname`, and `auto_join_room` are optional. RRC-TUI listens for
hub announcements on the network (press F6 to open hub discovery), and if no
nickname is set, your identity hash will be used as your display name.

See [CONFIG.md](CONFIG.md) for detailed configuration options.

## Usage

Start the TUI client:

```bash
rrc-tui
```

### Keyboard Shortcuts

- **F1**: Show help
- **F2**: Join a room
- **F3**: Part from active room
- **F4**: Connect to hub
- **F5**: Disconnect from hub
- **F6**: Discover available hubs
- **F10**: Quit application
- **Enter**: Send message
- **Up/Down**: Navigate input history

### Message Status Indicators

Your sent messages show delivery status through color coding:

- **Yellow**: Message sent, waiting for hub confirmation
- **Green**: Message successfully delivered and confirmed by hub
- **Red**: Message timed out (may not have been received)
- **Magenta**: Slash command sent (no echo expected)

Regular messages that timeout after 30 seconds will show

`[TIMEOUT - message may not have been received]`

appended to the text.

Slash commands (starting with `/`) appear immediately in magenta.

### Quick Start

1. Launch `rrc-tui`
2. Press **F6** to discover available hubs (or configure hub_hash manually)
3. Press **F4** to connect to the hub
4. Press **F2** to join a room
5. Type a message and press **Enter** to send
6. Watch your message turn from yellow to green when confirmed
7. Navigate between rooms using the room list on the left
8. Press **F10** to quit


## Default File Locations

- Configuration: `~/.rrc-tui/config.json`
- Identity: `~/.rrc-tui/identity`
- Logs: `~/.rrc-tui/logs/rrc-tui.log`

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Related Projects

- [Reticulum](https://reticulum.network/) - The underlying network stack
- [rrcd](../rrcd) - Reticulum Relay Chat Hub daemon
- [rrc-gui](../rrc-gui) - GUI client for RRC using wxWidgets