"""EXIT-C — MFE velocity EDA.

Hypothèse : MFE velocity = mfe_bps / mfe_at_h (bps/h) prédit le final outcome.

Pour chaque trade dans baseline backtest, mesurer:
- mfe_at_h : quand MFE a été atteint (heures depuis entry)
- mfe_velocity = mfe_bps / max(mfe_at_h, 0.5) (en bps/h)
- final pnl_bps

Binning velocity en quartiles. Si Q1 (fast MFE) vs Q4 (slow MFE) ont des outcomes significativement différents → MFE velocity est exploitable comme signal exit.

Design rule potentiel : "at hours_held = T, si mfe_at_h < 4h AND velocity > X → trade probable winner, hold ; else considerer cut early".
"""
import json
import numpy as np
import time

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
TWENTYFOUR_M = 24 * 30 * 24 * 3600 * 1000
start_ts = latest_ts - TWENTYFOUR_M

early = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)
runner = ({"strategies": RUNNER_EXT_STRATEGIES, "extra_candles": RUNNER_EXT_HOURS // 4,
           "min_mfe_bps": RUNNER_EXT_MIN_MFE_BPS, "min_cur_to_mfe": RUNNER_EXT_MIN_CUR_TO_MFE}
          if RUNNER_EXT_STRATEGIES else None)

print(f"\nRunning baseline BT on 24m for trades dump...")
r = run_window(features, data, sector_features, dxy, start_ts, latest_ts,
               start_capital=1000.0, oi_data=oi, early_exit_params=early,
               runner_extension=runner, funding_data=funding,
               apply_adaptive_modulator=True,
               max_notional_per_trade=500.0, margin_check=True)
trades = r["trades"]
print(f"  {len(trades)} trades collected, PnL {r['pnl_pct']:+.2f}%, DD {r['max_dd_pct']:+.2f}%")

# Build per-trade records with MFE velocity
records = []
for t in trades:
    mfe = t.get("mfe_bps", 0)
    mae = t.get("mae_bps", 0)
    mfe_held = t.get("mfe_held", 0)  # candle index when MFE was set
    mfe_at_h = mfe_held * 4  # 4h per candle
    hold_h = (t["exit_t"] - t["entry_t"]) / 3_600_000  # ms to hours
    net = t.get("net", 0)
    strat = t.get("strat", "?")
    direction = t.get("dir", 0)
    reason = t.get("reason", "?")
    # MFE velocity in bps/h (avoid div by 0 — use 4h min)
    velocity = mfe / max(mfe_at_h, 4.0)
    records.append({
        "strat": strat, "dir": direction, "reason": reason,
        "mfe": mfe, "mae": mae, "mfe_at_h": mfe_at_h, "hold_h": hold_h,
        "net": net, "velocity": velocity,
    })

print(f"\n=== Distribution per strat ===")
strats = sorted(set(r["strat"] for r in records))
for st in strats:
    rs = [r for r in records if r["strat"] == st]
    if len(rs) < 10:
        continue
    nets = np.array([r["net"] for r in rs])
    mfes = np.array([r["mfe"] for r in rs])
    mfes_at_h = np.array([r["mfe_at_h"] for r in rs])
    vels = np.array([r["velocity"] for r in rs])
    print(f"  {st:>4} n={len(rs):>4}  mfe μ={mfes.mean():>6.0f}  mfe_at_h μ={mfes_at_h.mean():>5.1f}h  "
          f"vel μ={vels.mean():>5.0f}  net μ={nets.mean():>+7.1f}")

# Velocity quartile analysis per strat
print(f"\n=== MFE velocity quartile analysis (forward outcome = net pnl bps) ===")
for st in strats:
    rs = [r for r in records if r["strat"] == st and r["mfe"] > 0 and r["mfe_at_h"] > 0]
    if len(rs) < 40:
        continue
    print(f"\n  --- {st} (n={len(rs)}) ---")
    vels = np.array([r["velocity"] for r in rs])
    nets = np.array([r["net"] for r in rs])
    q_thresh = np.percentile(vels, [25, 50, 75])
    print(f"  Velocity quartile thresholds (bps/h): {q_thresh}")
    print(f"  {'Quartile':<20} {'n':>5} {'net μ':>8} {'net med':>9} {'WR':>6} {'mfe μ':>8} {'mfe_at_h μ':>12}")
    bins = [
        ("Q1 (slow MFE)", vels <= q_thresh[0]),
        ("Q2", (vels > q_thresh[0]) & (vels <= q_thresh[1])),
        ("Q3", (vels > q_thresh[1]) & (vels <= q_thresh[2])),
        ("Q4 (fast MFE)", vels > q_thresh[2]),
    ]
    for name, mask in bins:
        if mask.sum() == 0:
            continue
        rs_b = [rs[i] for i in range(len(rs)) if mask[i]]
        n_b = mask.sum()
        net_mu = nets[mask].mean()
        net_med = float(np.median(nets[mask]))
        wr = (nets[mask] > 0).mean() * 100
        mfe_b = np.mean([r["mfe"] for r in rs_b])
        h_b = np.mean([r["mfe_at_h"] for r in rs_b])
        print(f"  {name:<20} {n_b:>5} {net_mu:>+8.1f} {net_med:>+9.1f} {wr:>5.1f}% {mfe_b:>+8.0f} {h_b:>11.1f}h")

# Check correlation velocity vs net by strat
print(f"\n=== Correlation velocity → net pnl (per strat) ===")
for st in strats:
    rs = [r for r in records if r["strat"] == st and r["mfe"] > 0 and r["mfe_at_h"] > 0]
    if len(rs) < 40:
        continue
    vels = np.array([r["velocity"] for r in rs])
    nets = np.array([r["net"] for r in rs])
    corr = float(np.corrcoef(vels, nets)[0, 1])
    print(f"  {st}: n={len(rs)}, corr(velocity, net) = {corr:+.3f}")

# Specific scenarios: trades that REACH MFE early but END BADLY
print(f"\n=== Fast-MFE losers (early MFE but bad outcome) ===")
for st in strats:
    rs = [r for r in records if r["strat"] == st]
    fast_losers = [r for r in rs if r["mfe_at_h"] <= 4.0 and r["mfe"] > 200 and r["net"] < 0]
    if len(fast_losers) > 5:
        avg_loss = np.mean([r["net"] for r in fast_losers])
        avg_mfe = np.mean([r["mfe"] for r in fast_losers])
        avg_mae = np.mean([r["mae"] for r in fast_losers])
        print(f"  {st}: {len(fast_losers)} fast-MFE losers  (avg mfe={avg_mfe:.0f}, "
              f"avg mae={avg_mae:.0f}, avg final net={avg_loss:.0f})")
        print(f"     → potentiel exit rule: \"si mfe_at_h ≤ 4h ET mfe ≥ 200 bps ET unrealized < 0 → ?\"")

# Save
with open("/home/crypto/backtests/output/eda_mfe_velocity.json", "w") as f:
    json.dump({
        "n_trades": len(records),
        "by_strat": {st: {"n": sum(1 for r in records if r["strat"] == st)} for st in strats},
    }, f, indent=2)
print(f"\nResults saved to backtests/output/eda_mfe_velocity.json")
