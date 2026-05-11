"""Cross-module synchronization primitives.

Single global write lock that serializes all SQLite writes from the scan,
API, and collector threads. Imported by db.py, persistence.py, web.py,
collector.py — kept in its own module to avoid awkward local imports.
"""

from __future__ import annotations

import threading

# Global write lock — serialises all SQLite writes from scan, API, and collector threads.
db_lock = threading.Lock()
