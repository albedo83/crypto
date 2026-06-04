"""Compare paper bot performance vs backtest over the exact same window.

Paper reset 2026-05-30 ~20:00 UTC. End = latest BTC candle.
$1000 capital, same modulators/exits as production.
"""
from datetime import datetime, timezone
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
    RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
    RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
)

print("Loading data...")
data = load_3y_candles()
features = build_features(data)
sector_features = compute_sector_features(features, data)
dxy = load_dxy()
oi = load_oi()
funding = load_funding()

latest_ts = max(c["t"] for c in data["BTC"])
end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
print(f"Data ends at {end_dt.isoformat()}")

# Paper reset window
start_dt = datetime(2026, 5, 30, 20, 0, 0, tzinfo=timezone.utc)
start_ts = int(start_dt.timestamp() * 1000)

print(f"\nBacktest window: {start_dt.isoformat()} → {end_dt.isoformat()}")
print(f"Capital: $1000")

early_exit_params = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)
runner_ext_cfg = ({
    "strategies": RUNNER_EXT_STRATEGIES,
    "extra_candles": RUNNER_EXT_HOURS // 4,
    "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS,
    "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE,
} if RUNNER_EXT_STRATEGIES else None)

r = run_window(features, data, sector_features, dxy,
               start_ts, latest_ts,
               start_capital=1000.0,
               oi_data=oi,
               early_exit_params=early_exit_params,
               runner_extension=runner_ext_cfg,
               funding_data=funding,
               apply_adaptive_modulator=True)

print(f"\n=== Backtest result ===")
print(f"  End capital:   ${r['end_capital']:.2f}")
print(f"  PnL net:       ${r['end_capital'] - 1000:+.2f}  ({r['pnl_pct']:+.2f}%)")
print(f"  N trades:      {r['n_trades']}")
print(f"  Max DD:        {r['max_dd_pct']:.2f}%")
print(f"  WR:            {r.get('wr_pct', 'N/A')}%" if r.get('wr_pct') is not None else "  WR:            N/A")

trades = r.get('trades', [])
print(f"\n=== Trades closed by BT during window ===")
if not trades:
    print("  (no closed trades)")
else:
    print(f"{'entry':22} {'sym':6} {'dir':>4} {'strat':5} {'size':>8} {'pnl':>8} {'bps':>6} {'h':>5} reason")
    for t in trades:
        et = datetime.fromtimestamp(t['entry_t']/1000, tz=timezone.utc).isoformat()[:19]
        sym = t.get('coin', '?')
        d = '+1' if t.get('dir', 0) > 0 else '-1'
        s = t.get('strat', '?')
        sz = t.get('size_usdt', 0)
        pnl = t.get('pnl', 0)
        nb = t.get('net_bps', 0)
        h = t.get('hold_h', 0)
        rs = t.get('reason', '?')
        print(f"{et:22} {sym:6} {d:>4} {s:5} {sz:>8.0f} {pnl:>8.2f} {nb:>6.0f} {h:>5.1f} {rs}")

# Open positions at end of window (not closed)
open_pos = r.get('open_positions', [])
if open_pos:
    print(f"\n=== Open positions at window end (not yet closed by BT) ===")
    for p in open_pos:
        et = datetime.fromtimestamp(p['entry_t']/1000, tz=timezone.utc).isoformat()[:19]
        print(f"  {p.get('coin'):6} dir={p.get('dir')} {p.get('strat'):4} size=${p.get('size_usdt',0):.0f} entry_t={et}")
