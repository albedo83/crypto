"""Alfred entry point — MarketDataMaster + BotInstances + unified web app.

    python3 -m alfred

Env (all optional):
    ALFRED_DATA_DIR       default alfred/data
    ALFRED_BOTS_CONFIG    default alfred/bots.json (no file = 0 bots, pure
                          observation mode)
    ALFRED_WEB_PORT       default 8101
    ALFRED_ROOT_PATH      nginx subpath (e.g. /alfred)
    ALFRED_REST_POLL      metaAndAssetCtxs cadence seconds (default 60 during
                          the observation/parallel-run phase; production 20)
    ALFRED_CANDLE_SLEEP   inter-symbol sleep for REST candle fetches (default 1.0)
    TG_BOT_TOKEN/TG_CHAT_ID  master system alerts (label ALFRED)
    DASHBOARD_USER/PASS/AUTH_SALT  web auth
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ALFRED] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("alfred")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TICK_SECONDS = 20.0          # exit-chain cadence (manual_stop latency ceiling)
SCAN_SECONDS = 3600.0        # full scan cadence
BOUNDARY_GRACE_S = 180.0     # post-4h-close grace before the entry scan


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


async def scheduler(master, bots: dict, shutdown: asyncio.Event):
    """Owns the trading cadences: 20s ticks (exits) and hourly / 4h-boundary
    scans (snapshot refresh + entries). The master's own hourly loop keeps
    the data-health duties (gap repair, WS audit, status line)."""
    last_scan = 0.0
    while master.running:
        t0 = time.time()
        try:
            # No prices yet (first REST poll in flight) → nothing meaningful
            # to tick or scan; rushing would burn the 4h entry gate on empty
            # signals (st.price == 0 → no candidates).
            if not master.last_price_fetch:
                await asyncio.sleep(1.0)
                continue
            for b in bots.values():
                await asyncio.to_thread(b.safe_on_tick)
            # Live bots: cheap equity refresh at the tick cadence (2 SDK
            # calls per live bot — phase 4).
            for b in bots.values():
                if b.broker.is_live:
                    await asyncio.to_thread(b.safe_refresh_equity)

            now_t = time.time()
            last_4h = (int(now_t) // 14400) * 14400
            # Les bots stopped ne scannent jamais (safe_on_scan sort avant
            # on_scan) → les inclure laisserait post_4h vrai en permanence
            # (scan-storm 20 s). Un bot paused consomme son gate dans on_scan.
            post_4h = (now_t - last_4h >= BOUNDARY_GRACE_S
                       and any(b._last_entry_scan_4h_close < last_4h
                               for b in bots.values()
                               if b.status != "stopped"))
            if now_t - last_scan >= SCAN_SECONDS or post_4h:
                log.info("Scan (trigger: %s)",
                         "4h-boundary" if post_4h and now_t - last_scan < SCAN_SECONDS
                         else "hourly")
                master.snapshot = await asyncio.to_thread(master.build_snapshot)
                await asyncio.to_thread(master.log_market_snapshot)
                for b in bots.values():
                    await asyncio.to_thread(b.safe_on_scan)
                # Live bots: hourly reconcile + full account diagnostics
                for b in bots.values():
                    if b.broker.is_live:
                        await asyncio.to_thread(b.safe_reconcile)
                        await asyncio.to_thread(b.safe_refresh_equity, True)
                last_scan = now_t
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("scheduler error")
        await asyncio.sleep(max(1.0, TICK_SECONDS - (time.time() - t0)))


async def run():
    from alfred import ALFRED_VERSION
    from alfred.botinstance import BotInstance
    from alfred.db import Database
    from alfred.market import MarketDataMaster
    from alfred.settings import DEFAULT_PARAMS, load_bots_config
    from alfred.telegram import Notifier

    data_dir = os.environ.get(
        "ALFRED_DATA_DIR", os.path.join(_REPO_ROOT, "alfred", "data"))
    os.makedirs(data_dir, exist_ok=True)

    lock_file = open(os.path.join(data_dir, "alfred.lock"), "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("Another Alfred instance holds %s/alfred.lock — exiting", data_dir)
        sys.exit(1)

    notifier = Notifier(
        token=os.environ.get("TG_BOT_TOKEN", ""),
        chat_id=os.environ.get("TG_CHAT_ID", ""),
        categories=os.environ.get("ALFRED_TG_CATEGORIES", "system,security"),
        label="ALFRED")

    db = Database(os.path.join(data_dir, "market.db"), "market")
    master = MarketDataMaster(
        DEFAULT_PARAMS, db, notifier, data_dir,
        rest_poll_seconds=float(os.environ.get("ALFRED_REST_POLL", "60")),
        candle_fetch_sleep=float(os.environ.get("ALFRED_CANDLE_SLEEP", "1.0")),
    )

    # ── Bots ──
    bots: dict[str, BotInstance] = {}
    bots_cfg_path = os.environ.get(
        "ALFRED_BOTS_CONFIG", os.path.join(_REPO_ROOT, "alfred", "bots.json"))
    if os.path.exists(bots_cfg_path):
        for cfg in load_bots_config(bots_cfg_path):
            bots[cfg.id] = BotInstance(cfg, master, data_dir)
            log.info("Bot configured: %s (%s, capital $%.0f%s)",
                     cfg.id, cfg.mode, cfg.capital_initial,
                     ", paused" if cfg.start_paused else "")
    else:
        log.info("No bots.json (%s) — observation mode, 0 bots", bots_cfg_path)

    shutdown = asyncio.Event()

    def _sig(signum, frame):
        log.info("Shutdown signal (%s)", signum)
        master.running = False
        shutdown.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    log.info("Alfred v%s — %d symbols, %d bot(s), poll %.0fs, data %s",
             ALFRED_VERSION, len(DEFAULT_PARAMS.all_symbols), len(bots),
             master.rest_poll_seconds, data_dir)
    db.log_event("MASTER_START", None, {
        "version": ALFRED_VERSION, "bots": sorted(bots.keys())})
    notifier.send(f"🤖 Alfred v{ALFRED_VERSION} démarré "
                  f"({len(bots)} bot(s): {', '.join(sorted(bots)) or 'aucun'})",
                  category="system")

    await master.backfill()
    master.snapshot = await asyncio.to_thread(master.build_snapshot)
    log.info("Initial snapshot v%d: btc_z=%s alt_index=%+.0f",
             master.snapshot.version,
             f"{master.snapshot.btc_z:+.2f}" if master.snapshot.btc_z is not None else "n/a",
             master.snapshot.alt_index)
    for b in bots.values():
        b.load()
        # Live bots (phase 4): drop ghosts / flag orphans once, then prime
        # the equity card so the dashboard isn't blank until the first tick.
        if b.broker.is_live:
            await asyncio.to_thread(b.boot_reconcile)
            await asyncio.to_thread(b.safe_refresh_equity, True)

    # ── Web app ──
    import uvicorn
    from alfred.web.app import create_app
    web_port = int(os.environ.get("ALFRED_WEB_PORT", "8101"))
    # v1.15.0 : bind loopback par défaut — nginx (127.0.0.1:8101) est le seul
    # consommateur légitime ; 0.0.0.0 exposait le port en clair sur Internet
    # (court-circuit TLS). Override : ALFRED_WEB_HOST=0.0.0.0.
    web_host = os.environ.get("ALFRED_WEB_HOST", "127.0.0.1")
    app = create_app(bots, master)
    server = uvicorn.Server(uvicorn.Config(app, host=web_host, port=web_port,
                                           log_level="warning"))
    log.info("Web app on :%d (root_path=%r)", web_port,
             os.environ.get("ALFRED_ROOT_PATH", ""))

    tasks = [
        asyncio.create_task(master.ws_loop(), name="ws"),
        asyncio.create_task(master.poll_loop(), name="poll"),
        asyncio.create_task(master.hourly_loop(), name="hourly"),
        asyncio.create_task(scheduler(master, bots, shutdown), name="scheduler"),
        asyncio.create_task(server.serve(), name="web"),
    ]
    await shutdown.wait()
    server.should_exit = True
    for b in bots.values():
        try:
            b._save_state()
        except Exception:
            log.exception("[%s] final save_state failed", b.id)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    master.flow.flush_all()
    db.log_event("MASTER_STOP")
    db.close()
    for b in bots.values():
        b.db.close()
    log.info("Alfred stopped cleanly")


if __name__ == "__main__":
    _load_env()
    asyncio.run(run())
