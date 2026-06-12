"""Daily tracker comparing paper bot live equity vs BT canonical over the same window.

Reads config from analysis/output/paper_tracker_config.json (reset_ts, start_capital).
Refreshes 4h candles + OI + funding, runs run_window from reset_ts → latest candle,
computes paper equity-equivalent (realized + unrealized), appends one JSON line to log.

Usage:
    python3 -m backtests.paper_vs_bt_tracker          # full run
    python3 -m backtests.paper_vs_bt_tracker --no-fetch # skip data refresh
    python3 -m backtests.paper_vs_bt_tracker --quiet   # only emit log line
"""
import json, os, sys, sqlite3, subprocess, time
from datetime import datetime, timezone

# v12.17.4: ensure the repo root is on sys.path so `from backtests...` imports
# work regardless of how the script is invoked (cron, direct script, -m module).
sys.path.insert(0, "/home/crypto")

CONFIG_PATH = "/home/crypto/analysis/output/paper_tracker_config.json"
LOG_PATH = "/home/crypto/analysis/output/paper_vs_bt_tracker.log"
STATE_PATH = "/home/crypto/alfred/data/bots/paper/state.json"
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


def paper_equity():
    """Read paper state + last ticks to compute realized + unrealized."""
    state = json.load(open(STATE_PATH))
    capital = state.get("capital", 1000.0)
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


def run_bt(reset_ts, start_capital):
    from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
    from backtests.backtest_genetic import load_3y_candles, build_features
    from backtests.backtest_sector import compute_sector_features
    from analysis.bot.config import (
        DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
        DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
        RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
        RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
    )
    log("Loading BT data...")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy = load_dxy()
    oi = load_oi()
    funding = load_funding()
    latest_ts = max(c["t"] for c in data["BTC"])
    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    runner_ext = ({
        "strategies": RUNNER_EXT_STRATEGIES,
        "extra_candles": RUNNER_EXT_HOURS // 4,
        "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
        "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
    } if RUNNER_EXT_STRATEGIES else None)
    start_ts_ms = int(reset_ts * 1000)
    log(f"Running BT {datetime.fromtimestamp(reset_ts, tz=timezone.utc).isoformat()} "
        f"→ {datetime.fromtimestamp(latest_ts/1000, tz=timezone.utc).isoformat()}")
    r = run_window(features, data, sector_features, dxy,
                   start_ts_ms, latest_ts, start_capital=start_capital,
                   oi_data=oi, early_exit_params=early_exit,
                   runner_extension=runner_ext, funding_data=funding,
                   apply_adaptive_modulator=True)
    return {"end_capital": round(r["end_capital"], 2),
            "pnl": round(r["end_capital"] - start_capital, 2),
            "pnl_pct": round(r["pnl_pct"], 2),
            "n_trades": r["n_trades"],
            "max_dd_pct": round(r["max_dd_pct"], 2),
            "latest_candle_iso": datetime.fromtimestamp(latest_ts/1000, tz=timezone.utc).isoformat()}


def main():
    cfg = json.load(open(CONFIG_PATH))
    reset_ts = cfg["reset_ts"]
    start_capital = cfg["start_capital"]
    threshold = cfg.get("telegram_alert_threshold_pct", 5.0)

    if not NO_FETCH:
        refresh_data()

    paper = paper_equity()
    bt = run_bt(reset_ts, start_capital)

    paper_pct = (paper["equity"] - start_capital) / start_capital * 100
    gap_pct = bt["pnl_pct"] - paper_pct

    age_days = (time.time() - reset_ts) / 86400

    record = {
        "ts": int(time.time()),
        "iso": datetime.now(timezone.utc).isoformat(),
        "age_days": round(age_days, 2),
        "reset_iso": cfg["reset_iso"],
        "paper": paper,
        "bt": bt,
        "paper_pct": round(paper_pct, 2),
        "gap_pp": round(gap_pct, 2),
    }

    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")

    log(f"\n=== Tracker {record['iso']} (D+{age_days:.1f}d) ===")
    log(f"Paper:   equity=${paper['equity']:.2f}  realized=${paper['realized']:+.2f}  "
        f"unrealized=${paper['unrealized']:+.2f}  ({paper_pct:+.2f}%)  open={paper['n_open']}")
    log(f"BT:      end=${bt['end_capital']:.2f}  pnl=${bt['pnl']:+.2f}  ({bt['pnl_pct']:+.2f}%)  "
        f"trades={bt['n_trades']}  DD={bt['max_dd_pct']:.2f}%")
    log(f"Gap:     {gap_pct:+.2f}pp (BT - Paper)")

    if abs(gap_pct) >= threshold:
        msg = (f"Tracker D+{age_days:.1f}d: gap {gap_pct:+.2f}pp\n"
               f"Paper {paper_pct:+.2f}% (${paper['equity']:.2f})\n"
               f"BT    {bt['pnl_pct']:+.2f}% (${bt['end_capital']:.2f}, {bt['n_trades']} tr)\n"
               f"Reset {cfg['reset_iso']}")
        send_telegram(msg)
        log(f"[telegram] alert sent (gap {gap_pct:+.2f}pp ≥ {threshold}pp)")


if __name__ == "__main__":
    main()
