"""RRC TUI - Text User Interface client for RRC (Reticulum Relay Chat).

Supports both Urwid and Textual TUI frameworks.
"""

__version__ = "0.1.0"

# Import the main entry point
from .main import main

__all__ = ["main", "__version__"]
