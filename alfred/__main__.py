"""Alfred entry point.

Phase 2: MarketDataMaster only (0 bots) — observation deployment running
alongside the legacy bots. Phase 3 adds BotInstances + the unified web app.

    python3 -m alfred

Env (all optional):
    ALFRED_DATA_DIR       default alfred/data
    ALFRED_REST_POLL      metaAndAssetCtxs cadence seconds (default 60 —
                          observation-friendly; production target is 20)
    ALFRED_CANDLE_SLEEP   inter-symbol sleep for REST candle fetches (default 1.0)
    TG_BOT_TOKEN/TG_CHAT_ID  master system alerts (label ALFRED)
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ALFRED] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("alfred")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env():
    path = os.path.join(_REPO_ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


async def run():
    from alfred import ALFRED_VERSION
    from alfred.db import Database
    from alfred.market import MarketDataMaster
    from alfred.settings import DEFAULT_PARAMS
    from alfred.telegram import Notifier

    data_dir = os.environ.get(
        "ALFRED_DATA_DIR", os.path.join(_REPO_ROOT, "alfred", "data"))
    os.makedirs(data_dir, exist_ok=True)

    # Single-instance lock
    lock_file = open(os.path.join(data_dir, "alfred.lock"), "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("Another Alfred instance holds %s/alfred.lock — exiting", data_dir)
        sys.exit(1)

    notifier = Notifier(
        token=os.environ.get("TG_BOT_TOKEN", ""),
        chat_id=os.environ.get("TG_CHAT_ID", ""),
        categories=os.environ.get("ALFRED_TG_CATEGORIES", "system"),
        label="ALFRED")

    db = Database(os.path.join(data_dir, "market.db"), "market")
    master = MarketDataMaster(
        DEFAULT_PARAMS, db, notifier, data_dir,
        rest_poll_seconds=float(os.environ.get("ALFRED_REST_POLL", "60")),
        candle_fetch_sleep=float(os.environ.get("ALFRED_CANDLE_SLEEP", "1.0")),
    )

    shutdown = asyncio.Event()

    def _sig(signum, frame):
        log.info("Shutdown signal (%s)", signum)
        master.running = False
        shutdown.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    log.info("Alfred v%s — MarketDataMaster observation mode (%d symbols, "
             "poll %.0fs, data %s)", ALFRED_VERSION,
             len(DEFAULT_PARAMS.all_symbols), master.rest_poll_seconds, data_dir)
    db.log_event("MASTER_START", None, {"version": ALFRED_VERSION,
                                        "mode": "observation"})
    notifier.send(f"🤖 Alfred v{ALFRED_VERSION} démarré (observation, 0 bot)",
                  category="system")

    await master.backfill()
    master.snapshot = await asyncio.to_thread(master.build_snapshot)
    log.info("Initial snapshot v%d: btc_z=%s alt_index=%+.0f",
             master.snapshot.version,
             f"{master.snapshot.btc_z:+.2f}" if master.snapshot.btc_z is not None else "n/a",
             master.snapshot.alt_index)

    tasks = [
        asyncio.create_task(master.ws_loop(), name="ws"),
        asyncio.create_task(master.poll_loop(), name="poll"),
        asyncio.create_task(master.hourly_loop(), name="hourly"),
    ]
    await shutdown.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    master.flow.flush_all()
    db.log_event("MASTER_STOP")
    db.close()
    log.info("Alfred stopped cleanly")


if __name__ == "__main__":
    _load_env()
    asyncio.run(run())
