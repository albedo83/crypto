"""IDs, stream names, table mappings."""

# Venue ID (populated from DB at startup)
VENUE_ID: int = 1

# NOTE: open_interest is polled via REST every 5 minutes (see OIPoller).
# It is NOT a WebSocket micro-data stream — do not treat OI timestamps
# as sub-second precision. Compare with caution against tick-level data.

# Stream type -> table mapping
STREAM_TABLE = {
    "aggTrade": "trades_raw",
    "bookTicker": "book_tob",
    "depth10@100ms": "book_levels",
    "depth20@100ms": "book_levels",
    "markPrice@1s": "mark_index",
    "markPrice": "mark_index",
    "forceOrder": "liquidations",
}

# Tables written by collector
ALL_TABLES = [
    "trades_raw",
    "book_tob",
    "book_levels",
    "mark_index",
    "funding",
    "open_interest",
    "liquidations",
]

# PG NOTIFY channel
CONTROL_CHANNEL = "collector_control"

# Collector event types
EVENT_START = "start"
EVENT_STOP = "stop"
EVENT_WS_CONNECT = "ws_connect"
EVENT_WS_DISCONNECT = "ws_disconnect"
EVENT_WS_RECONNECT = "ws_reconnect"
EVENT_WS_ROTATE = "ws_rotate"
EVENT_ERROR = "error"
EVENT_SYMBOL_ADD = "symbol_add"
EVENT_SYMBOL_REMOVE = "symbol_remove"
