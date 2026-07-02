"""Daily tracker comparing each Alfred bot's live equity vs the canonical BT.

Reads a list of bots from analysis/output/paper_tracker_config.json (each with
reset_ts, start_capital, state_path, dca flag). Refreshes 4h candles + OI +
funding, loads the BT dataset ONCE, then for each bot runs run_window from its
reset_ts → latest candle and computes the equity-equivalent gap.

Non-DCA bots (paper/live/junior) report a clean % gap and can trigger a Telegram
alert when |gap| ≥ threshold. DCA bots (baby) report trading P&L in $ only — a %
gap is meaningless once capital is injected over the window — and never alert.

Usage:
    python3 -m backtests.paper_vs_bt_tracker            # full run
    python3 -m backtests.paper_vs_bt_tracker --no-fetch # skip data refresh
    python3 -m backtests.paper_vs_bt_tracker --quiet    # only emit log lines
"""
import json, os, sys, sqlite3, subprocess, time
from datetime import datetime, timezone

# v12.17.4: ensure the repo root is on sys.path so `from backtests...` imports
# work regardless of how the script is invoked (cron, direct script, -m module).
sys.path.insert(0, "/home/crypto")

CONFIG_PATH = "/home/crypto/analysis/output/paper_tracker_config.json"
LOG_PATH = "/home/crypto/analysis/output/paper_vs_bt_tracker.log"
DB_PATH = "/home/crypto/alfred/data/market.db"
ENV_PATH = "/home/crypto/.env"

QUIET = "--quiet" in sys.argv
NO_FETCH = "--no-fetch" in sys.argv
NO_TELEGRAM = "--no-telegram" in sys.argv


def log(msg):
    if not QUIET:
        print(msg, flush=True)


def load_env():
    env = {}
    if not os.path.exists(ENV_PATH):
        return env
    with open(ENV_PATH) as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.strip().partition("=")
                env[k] = v.strip().strip('"').strip("'")
    return env


def send_telegram(text):
    if NO_TELEGRAM:
        return
    env = load_env()
    token = env.get("TG_BOT_TOKEN")
    chat = env.get("TG_CHAT_ID")
    if not token or not chat:
        log("[telegram] no creds, skip")
        return
    import urllib.request, urllib.parse
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        log(f"[telegram] failed: {e}")


def load_config():
    cfg = json.load(open(CONFIG_PATH))
    threshold = cfg.get("telegram_alert_threshold_pct", 5.0)
    if "bots" in cfg:
        return cfg["bots"], threshold
    # Backward-compat: legacy single-paper config.
    return [{"id": "paper", "reset_ts": cfg["reset_ts"], "reset_iso": cfg["reset_iso"],
             "start_capital": cfg["start_capital"], "dca": False,
             "state_path": "/home/crypto/alfred/data/bots/paper/state.json",
             "label": cfg.get("label", "")}], threshold


def refresh_data():
    log("Refreshing data...")
    procs = []
    for mod in ("backtests.fetch_4h_candles", "backtests.fetch_oi_history", "backtests.fetch_funding_history"):
        p = subprocess.Popen(["/home/crypto/.venv/bin/python3", "-m", mod],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append((mod, p))
    for mod, p in procs:
        p.wait()
        log(f"  {mod} → rc={p.returncode}")


def bot_equity(state_path):
    """Read a bot's state + last ticks to compute realized + unrealized."""
    state = json.load(open(state_path))
    capital = state.get("capital", 0.0)
    realized = state.get("total_pnl", 0.0)
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    unrealized = 0.0
    open_pos = []
    for p in state.get("positions", []):
        sym = p["symbol"]
        d = p["direction"]
        entry = p["entry_price"]
        size = p["size_usdt"]
        c.execute("SELECT mark_px FROM ticks WHERE symbol=? ORDER BY ts DESC LIMIT 1", (sym,))
        row = c.fetchone()
        if not row:
            continue
        last = row[0]
        gross_bps = (last / entry - 1) * 10000 * d
        net_bps = gross_bps - 9 - 1  # HL 9 bps RT + 1 bps funding drag
        ur = size * net_bps / 10000
        unrealized += ur
        open_pos.append({"sym": sym, "strat": p["strategy"], "dir": d,
                         "size": size, "ur_bps": round(net_bps), "ur_usd": round(ur, 2)})
    db.close()
    equity = capital + realized + unrealized
    return {"capital": capital, "realized": round(realized, 2),
            "unrealized": round(unrealized, 2), "equity": round(equity, 2),
            "n_open": len(open_pos), "open": open_pos}


def load_bt_dataset():
    """Load the canonical BT dataset once (shared across all bots)."""
    from backtests.backtest_rolling import load_oi, load_funding, load_dxy
    from backtests.backtest_genetic import load_3y_candles, build_features
    from backtests.backtest_sector import compute_sector_features
    log("Loading BT data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    latest_ts = max(c["t"] for c in data["BTC"])
    return {"data": data, "features": features, "sector": sector_features,
            "dxy": load_dxy(), "oi": load_oi(), "funding": load_funding(),
            "latest_ts": latest_ts}


def run_bt(bt, reset_ts, start_capital):
    """Run the canonical (aligned) BT from reset_ts → latest candle.

    Aligned config = same as docs/backtests.md and the live bot: exits via
    evaluate_exit, HL margin cap, MFE on the mark (mfe_on_close).
    """
    from backtests.backtest_rolling import run_window
    start_ts_ms = int(reset_ts * 1000)
    r = run_window(bt["features"], bt["data"], bt["sector"], bt["dxy"],
                   start_ts_ms, bt["latest_ts"], start_capital=start_capital,
                   oi_data=bt["oi"], funding_data=bt["funding"],
                   apply_adaptive_modulator=True,
                   aligned=True, margin_check=True, mfe_on_close=True)
    return {"end_capital": round(r["end_capital"], 2),
            "pnl": round(r["end_capital"] - start_capital, 2),
            "pnl_pct": round(r["pnl_pct"], 2),
            "n_trades": r["n_trades"],
            "max_dd_pct": round(r["max_dd_pct"], 2),
            "latest_candle_iso": datetime.fromtimestamp(bt["latest_ts"] / 1000, tz=timezone.utc).isoformat()}


def main():
    bots, threshold = load_config()

    if not NO_FETCH:
        refresh_data()

    bt = load_bt_dataset()
    now = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    records = []       # one per bot, appended to the log
    alert_lines = []   # consolidated TG body (all bots), if any clean bot breaches
    breach = False

    for b in bots:
        eq = bot_equity(b["state_path"])
        dca = b.get("dca", False)
        # DCA bots: the % is computed on the TOTAL (current) capital — no longer a
        # pure inception return, but it gives an idea. The BT runs on the same base
        # so the gap stays coherent. Non-DCA bots use their fixed inception capital.
        base_capital = eq["capital"] if dca else b["start_capital"]
        r = run_bt(bt, b["reset_ts"], base_capital)
        age_days = (now - b["reset_ts"]) / 86400

        bot_pct = (eq["equity"] - base_capital) / base_capital * 100
        gap_pp = r["pnl_pct"] - bot_pct
        note = "  (base capital total, DCA)" if dca else ""
        tag = " (DCA)" if dca else ""
        log(f"\n=== {b['id']} D+{age_days:.1f}d ==={note}")
        log(f"  {b['id']}: equity=${eq['equity']:.2f}  ({bot_pct:+.2f}%)  "
            f"realized=${eq['realized']:+.2f}  unrealized=${eq['unrealized']:+.2f}  open={eq['n_open']}")
        log(f"  BT:    end=${r['end_capital']:.2f}  ({r['pnl_pct']:+.2f}%)  trades={r['n_trades']}  DD={r['max_dd_pct']:.2f}%")
        log(f"  Gap:   {gap_pp:+.2f}pp (BT - {b['id']})")
        alert_lines.append(f"{b['id']}{tag} D+{age_days:.1f}d: gap {gap_pp:+.2f}pp "
                           f"({bot_pct:+.2f}% vs BT {r['pnl_pct']:+.2f}%)")
        if abs(gap_pp) >= threshold:
            breach = True
        records.append({"ts": int(now), "iso": now_iso, "bot": b["id"], "dca": dca,
                        "age_days": round(age_days, 2), "reset_iso": b["reset_iso"],
                        "base_capital": round(base_capital, 2), "eq": eq, "bt": r,
                        "bot_pct": round(bot_pct, 2), "gap_pp": round(gap_pp, 2)})

    with open(LOG_PATH, "a") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")

    # One consolidated message (all bots) only if a non-DCA bot breaches threshold.
    if breach:
        send_telegram("Tracker vs BT — flotte\n" + "\n".join(alert_lines))
        log(f"\n[telegram] {'skipped (--no-telegram)' if NO_TELEGRAM else 'alert sent'} (≥ {threshold}pp breach)")
    else:
        log(f"\n[telegram] no breach (≥ {threshold}pp), no alert")


if __name__ == "__main__":
    main()
