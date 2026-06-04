"""EDA #3b — Half-life + simulated stat arb sur les 4 paires robustes.

Pour chaque paire :
1. Estimer half-life de mean-reversion via OU process : Δε = -θ × ε_{t-1} + noise
   half-life = ln(2) / θ
2. Simuler un trade simple : enter quand |z| > 2, exit quand z cross 0
3. Compte trades, win rate, avg PnL (en log-spread bps)
4. Coût ~26 bps RT (HL 9 bps × 2 legs + slippage 4 bps × 2)

Output: per-pair table avec half-life, n_trades simulés, avg_pnl after costs.
"""
import json
import numpy as np
from backtests.backtest_genetic import load_3y_candles

PAIRS = [("GALA", "SEI"), ("ADA", "PYTH"), ("BTC", "COMP"), ("ARB", "COMP")]
COST_BPS_RT = 26  # 9 × 2 fees + 4 × 2 slippage
ENTRY_Z = 2.0
EXIT_Z = 0.0

print("Loading data...")
data = load_3y_candles()
closes_by_ts = {sym: {c["t"]: c["c"] for c in cs if c["c"] > 0} for sym, cs in data.items()}

print(f"\nAnalyzing {len(PAIRS)} robust pairs...\n")
print(f"{'Pair':<14} {'half_life_h':>11} {'n_trades':>9} {'wr_%':>6} {'avg_pnl_bps':>12} {'profit?':>10}")
print("-" * 80)

summary = {}
for a, b in PAIRS:
    common_ts = sorted(set(closes_by_ts[a].keys()) & set(closes_by_ts[b].keys()))
    x = np.log(np.array([closes_by_ts[a][t] for t in common_ts]))
    y = np.log(np.array([closes_by_ts[b][t] for t in common_ts]))

    # Hedge ratio
    X = np.column_stack([np.ones_like(x), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    eps = y - X @ beta  # residual (spread)

    # OU process: Δε_t = -θ × ε_{t-1} + noise
    deps = np.diff(eps)
    eps_lag = eps[:-1]
    XL = np.column_stack([np.ones_like(eps_lag), eps_lag])
    coef, *_ = np.linalg.lstsq(XL, deps, rcond=None)
    theta = -coef[1]
    half_life_candles = np.log(2) / theta if theta > 0 else float("inf")
    half_life_hours = half_life_candles * 4  # 4h per candle

    # z-score
    spread_mu = float(eps.mean())
    spread_sd = float(eps.std())
    z = (eps - spread_mu) / spread_sd

    # Simulate simple stat-arb trades: enter when |z|>ENTRY, exit when |z|<EXIT
    # Each trade: pnl in spread bps = z_entry × spread_sd × 1e4
    in_trade = False
    entry_z = 0
    entry_side = 0  # +1 long spread (eps), -1 short spread
    trades_pnl = []
    for i in range(1, len(z)):
        if not in_trade:
            if z[i] > ENTRY_Z:
                in_trade = True
                entry_z = z[i]
                entry_side = -1  # short the spread (expect z to go down)
            elif z[i] < -ENTRY_Z:
                in_trade = True
                entry_z = z[i]
                entry_side = +1  # long the spread
        else:
            # Exit when |z| < EXIT or sign flip
            if entry_side == -1 and z[i] <= EXIT_Z:
                pnl_bps = (entry_z - z[i]) * spread_sd * 1e4 - COST_BPS_RT
                trades_pnl.append(pnl_bps)
                in_trade = False
            elif entry_side == +1 and z[i] >= -EXIT_Z:
                pnl_bps = (z[i] - entry_z) * spread_sd * 1e4 - COST_BPS_RT
                trades_pnl.append(pnl_bps)
                in_trade = False

    trades_pnl = np.array(trades_pnl) if trades_pnl else np.array([])
    n = len(trades_pnl)
    if n > 0:
        wr = float((trades_pnl > 0).mean() * 100)
        avg_pnl = float(trades_pnl.mean())
        total_pnl = float(trades_pnl.sum())
        profitable = "YES" if avg_pnl > 0 else "NO"
    else:
        wr, avg_pnl, total_pnl, profitable = 0, 0, 0, "no trades"

    summary[f"{a}/{b}"] = {
        "half_life_h": float(half_life_hours),
        "n_trades_sim": n,
        "wr_pct": wr,
        "avg_pnl_bps_after_costs": avg_pnl,
        "total_pnl_bps": total_pnl,
        "spread_sd_bps": spread_sd * 1e4,
        "profitable": profitable,
    }
    print(f"{a + '/' + b:<14} {half_life_hours:>11.1f} {n:>9} {wr:>6.1f} {avg_pnl:>+12.1f} {profitable:>10}")

# Save
with open("/home/crypto/backtests/output/eda_cointegration_halflife.json", "w") as f:
    json.dump(summary, f, indent=2)

print("\nNotes:")
print(f"  Cost assumption: {COST_BPS_RT} bps RT per pair trade (2 legs × HL 9 bps fees + 4 bps slippage)")
print(f"  Entry: |z| > {ENTRY_Z}, Exit: |z| < {EXIT_Z}")
print(f"  Each 'trade' = 1 paired buy/sell entry + exit")

print("\n=== VERDICT ===")
profitable_pairs = [(k, v) for k, v in summary.items() if v["avg_pnl_bps_after_costs"] > 0 and v["n_trades_sim"] >= 10]
print(f"  Profitable pairs (after costs, n>=10): {len(profitable_pairs)}")
for k, v in profitable_pairs:
    print(f"    {k}: avg={v['avg_pnl_bps_after_costs']:+.1f} bps, total={v['total_pnl_bps']:+.0f} bps over {v['n_trades_sim']} trades, "
          f"half-life={v['half_life_h']:.1f}h")
if not profitable_pairs:
    print("  → cointégration robuste mais pas tradable après coûts (spread trop étroit OU half-life trop longue)")
