"""Type definitions for RRC protocol structures.

Note: TypedDict with integer keys is not well-supported by type checkers,
so these are provided for documentation purposes. At runtime, use regular
dicts with integer keys as required by the CBOR protocol.
"""

from __future__ import annotations

from typing import Any

# Type aliases for RRC protocol structures
# These use standard types that work with the CBOR protocol

# HELLO body: {0: nick, 1: name, 2: version, 3: capabilities}
HelloBody = dict[int, str | dict[int, bool]]

# WELCOME body: {0: hub_name, 1: greeting, 2: version, 3: hub_limits}
WelcomeBody = dict[int, str | dict[int, bool | int]]

# JOINED body: {0: [user_hashes]}
JoinedBody = dict[int, list[bytes]]

# RESOURCE_ENVELOPE body: {0: id, 1: kind, 2: size, 3: sha256, 4: encoding}
ResourceEnvelopeBody = dict[int, bytes | str | int]

# Complete envelope structure: {0: version, 1: type, 2: id, 3: ts, 4: src, 5: room, 6: body, 7: nick}
Envelope = dict[int, int | bytes | str | Any]

# Type alias for common envelope body types
EnvelopeBody = (
    str
    | dict[int, Any]
    | list[bytes]
    | HelloBody
    | WelcomeBody
    | JoinedBody
    | ResourceEnvelopeBody
)
