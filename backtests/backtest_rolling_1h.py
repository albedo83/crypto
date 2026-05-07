"""Rolling backtest variant on 1h candles.

Same strategies, same feature semantics, finer execution grid. The
hypothesis is that running on 1h instead of 4h captures part of the
+517 bps slack on S5 winners observed in live (winners reach MFE then
revert before the 4h-bound timeout/exit can fire).

Constraints:
  - HL public /info serves 1h candles only ~200 days back (vs 3y for 4h),
    so walk-forward windows are limited to that range.
  - Feature lookback windows are scaled ×4 to preserve semantic time spans
    (ret_42h on 4h = 168 hours = 168 candles on 1h grid).
  - Feature field NAMES are kept identical (ret_6h, ret_42h, etc.) so that
    run_window's strategy logic is unchanged — just fed different data.

Usage:
    python3 -m backtests.backtest_rolling_1h
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from dateutil.relativedelta import relativedelta  # type: ignore

from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS, DEAD_TIMEOUT_SLACK_BPS,
)
from backtests.backtest_genetic import TOKENS, REF_TOKENS
from backtests.backtest_rolling import (
    load_dxy, load_funding, load_oi, run_window,
)
from backtests.backtest_sector import compute_sector_features

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
INTERVAL_HOURS = 1
SCALE = 4 // INTERVAL_HOURS  # candle-count scale vs 4h grid

# Time-equivalent lookbacks at 1h granularity
LB_RET_24H = 24 * SCALE   # 4h grid: 6, 1h grid: 24
LB_RET_7D = 42 * SCALE    # 168
LB_RET_14D = 84 * SCALE   # 336
LB_RET_30D = 180 * SCALE  # 720
LB_VOL_30D = LB_RET_30D


def load_1h_candles() -> dict:
    """Read all *_1h_3y.json into {coin: [candles]}."""
    out = {}
    for coin in TOKENS + REF_TOKENS:
        path = os.path.join(DATA_DIR, f"{coin}_1h_3y.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            data = json.load(f)
        # Normalize types — same convention as load_3y_candles in backtest_genetic
        for c in data:
            c["t"] = int(c["t"])
            c["o"] = float(c["o"])
            c["h"] = float(c["h"])
            c["l"] = float(c["l"])
            c["c"] = float(c["c"])
            c["v"] = float(c.get("v", 0))
        out[coin] = data
    return out


def build_features_1h(data: dict) -> dict:
    """Compute features on 1h candles using TIME-equivalent lookbacks.

    Same field names + semantics as build_features() in backtest_genetic.py
    but with candle counts scaled ×4 (since 1 hour = 1 candle here vs 4h
    candle = 1 candle in the 4h variant). Run_window's feature consumers
    operate on the field NAME, so by preserving names we keep the strategy
    logic unchanged — only the data it sees is finer.
    """
    btc_data = data.get("BTC", [])
    eth_data = data.get("ETH", [])

    btc_ret_by_t: dict[int, dict] = {}
    for i, c in enumerate(btc_data):
        r = {}
        if i >= LB_RET_7D and btc_data[i - LB_RET_7D]["c"] > 0:
            r["btc_7d"] = (c["c"] / btc_data[i - LB_RET_7D]["c"] - 1) * 1e4
        if i >= LB_RET_30D and btc_data[i - LB_RET_30D]["c"] > 0:
            r["btc_30d"] = (c["c"] / btc_data[i - LB_RET_30D]["c"] - 1) * 1e4
        btc_ret_by_t[c["t"]] = r

    eth_ret_by_t: dict[int, dict] = {}
    for i, c in enumerate(eth_data):
        r = {}
        if i >= LB_RET_7D and eth_data[i - LB_RET_7D]["c"] > 0:
            r["eth_7d"] = (c["c"] / eth_data[i - LB_RET_7D]["c"] - 1) * 1e4
        eth_ret_by_t[c["t"]] = r

    alt_coins = [c for c in TOKENS if c in data]
    all_alt_7d: dict[int, dict] = defaultdict(dict)
    for coin in alt_coins:
        candles = data[coin]
        for i, c in enumerate(candles):
            if i >= LB_RET_7D and candles[i - LB_RET_7D]["c"] > 0:
                ret = (c["c"] / candles[i - LB_RET_7D]["c"] - 1) * 1e4
                all_alt_7d[c["t"]][coin] = ret

    features: dict = {}
    for coin in alt_coins:
        candles = data[coin]
        n = len(candles)
        coin_features = []
        closes = np.array([c["c"] for c in candles])
        highs = np.array([c["h"] for c in candles])
        lows = np.array([c["l"] for c in candles])
        volumes = np.array([c["v"] for c in candles])

        # Warmup = longest lookback
        for i in range(LB_RET_30D, n):
            c = candles[i]
            t = c["t"]
            f = {"t": t}

            # Returns at semantic time spans
            if closes[i - LB_RET_24H] > 0:
                f["ret_6h"] = (closes[i] / closes[i - LB_RET_24H] - 1) * 1e4
            else:
                continue
            f["ret_42h"] = ((closes[i] / closes[i - LB_RET_7D] - 1) * 1e4
                            if i >= LB_RET_7D and closes[i - LB_RET_7D] > 0 else 0.0)
            f["ret_84h"] = ((closes[i] / closes[i - LB_RET_14D] - 1) * 1e4
                            if i >= LB_RET_14D and closes[i - LB_RET_14D] > 0 else 0.0)
            f["ret_180h"] = ((closes[i] / closes[i - LB_RET_30D] - 1) * 1e4
                             if i >= LB_RET_30D and closes[i - LB_RET_30D] > 0 else 0.0)

            # Volatility
            if i >= LB_RET_7D:
                rets_7d = np.diff(closes[i-LB_RET_7D:i+1]) / closes[i-LB_RET_7D:i]
                f["vol_7d"] = float(np.std(rets_7d) * 1e4) if len(rets_7d) > 1 else 0.0
            else:
                f["vol_7d"] = 0.0
            if i >= LB_VOL_30D:
                rets_30d = np.diff(closes[i-LB_VOL_30D:i+1]) / closes[i-LB_VOL_30D:i]
                f["vol_30d"] = float(np.std(rets_30d) * 1e4) if len(rets_30d) > 1 else 0.0
            else:
                f["vol_30d"] = 0.0
            f["vol_ratio"] = f["vol_7d"] / f["vol_30d"] if f["vol_30d"] > 0 else 1.0

            # 30d high/low for drawdown / recovery
            high_30d = float(np.max(highs[max(0, i-LB_RET_30D):i+1]))
            low_30d = float(np.min(lows[max(0, i-LB_RET_30D):i+1]))
            f["drawdown"] = (closes[i] / high_30d - 1) * 1e4 if high_30d > 0 else 0.0
            f["recovery"] = (closes[i] / low_30d - 1) * 1e4 if low_30d > 0 else 0.0
            f["range_pct"] = (c["h"] - c["l"]) / c["c"] * 1e4 if c["c"] > 0 else 0.0

            # Consec up/down — scale lookback
            consec_up = consec_dn = 0
            lb_consec = 20 * SCALE
            for j in range(i, max(i - lb_consec, 0), -1):
                if closes[j] > closes[j - 1]:
                    consec_up += 1
                else:
                    break
            for j in range(i, max(i - lb_consec, 0), -1):
                if closes[j] < closes[j - 1]:
                    consec_dn += 1
                else:
                    break
            f["consec_up"] = consec_up
            f["consec_dn"] = consec_dn

            br = btc_ret_by_t.get(t, {})
            f["btc_7d"] = br.get("btc_7d", 0.0)
            f["btc_30d"] = br.get("btc_30d", 0.0)
            er = eth_ret_by_t.get(t, {})
            f["eth_7d"] = er.get("eth_7d", 0.0)
            f["btc_eth_spread"] = f["btc_7d"] - f["eth_7d"]
            f["alt_vs_btc_7d"] = f["ret_42h"] - f["btc_7d"]
            f["alt_vs_btc_30d"] = f["ret_180h"] - f["btc_30d"]

            alt_rets = all_alt_7d.get(t, {})
            if len(alt_rets) >= 5:
                vals = list(alt_rets.values())
                f["alt_index_7d"] = float(np.mean(vals))
                f["dispersion_7d"] = float(np.std(vals))
                own_ret = alt_rets.get(coin, 0.0)
                f["alt_rank_7d"] = sum(1 for v in vals if v <= own_ret) / len(vals) * 100
            else:
                f["alt_index_7d"] = 0.0
                f["dispersion_7d"] = 0.0
                f["alt_rank_7d"] = 50.0

            if i >= LB_VOL_30D:
                vol_window = volumes[i-LB_VOL_30D:i]
                vol_mean = float(np.mean(vol_window))
                vol_std = float(np.std(vol_window))
                f["vol_z"] = (volumes[i] - vol_mean) / vol_std if vol_std > 0 else 0.0
            else:
                f["vol_z"] = 0.0

            f["_idx"] = i
            f["_close"] = closes[i]
            coin_features.append(f)
        features[coin] = coin_features
    return features


def main() -> None:
    print("Loading 1h candles...")
    t_load = time.time()
    data = load_1h_candles()
    n_candles = sum(len(v) for v in data.values())
    earliest = min(c[0]["t"] for c in data.values() if c)
    latest = max(c[-1]["t"] for c in data.values() if c)
    print(f"  {len(data)} coins, {n_candles} candles total "
          f"({datetime.fromtimestamp(earliest/1000, tz=timezone.utc).date()} → "
          f"{datetime.fromtimestamp(latest/1000, tz=timezone.utc).date()})")

    print("Building features (1h grid, ×4 lookback scaling)...")
    t0 = time.time()
    features = build_features_1h(data)
    print(f"  features built in {time.time()-t0:.0f}s, "
          f"{sum(len(v) for v in features.values())} feature rows")

    print("Computing sector features...")
    sector_features = compute_sector_features(features, data)

    print("Loading DXY / OI / funding data...")
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)

    # Walk-forward windows — limited by 1h history (~200d). Pick anchors that
    # all fit in the available data.
    windows = [
        ("6 mois", end_dt - relativedelta(months=6)),  # ~ start of the 1h archive
        ("3 mois", end_dt - relativedelta(months=3)),
        ("2 mois", end_dt - relativedelta(months=2)),
        ("1 mois", end_dt - relativedelta(months=1)),
    ]
    window_specs = [(lab, int(dt.timestamp() * 1000)) for lab, dt in windows]
    end_ts = latest_ts

    # exit_lead_candles needs to scale too (DEAD_TIMEOUT lead measured in hours)
    early_exit = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS / INTERVAL_HOURS),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )

    common = dict(
        sector_features=sector_features, dxy_data=dxy_data, end_ts_ms=end_ts,
        start_capital=500.0, oi_data=oi_data, early_exit_params=early_exit,
        funding_data=funding_data, interval_hours=INTERVAL_HOURS,
    )

    print("\n=== 1h backtest results ===")
    results = {}
    for label, start_ts in window_specs:
        t_w = time.time()
        r = run_window(features, data, start_ts_ms=start_ts, **common)
        results[label] = r
        s5 = r["by_strat"].get("S5", {"n": 0, "pnl": 0, "wr": 0})
        s9 = r["by_strat"].get("S9", {"n": 0, "pnl": 0, "wr": 0})
        s10 = r["by_strat"].get("S10", {"n": 0, "pnl": 0, "wr": 0})
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  "
              f"DD={r['max_dd_pct']:6.1f}%  "
              f"S5 n={s5['n']:3d}/${s5['pnl']:+.0f}  "
              f"S9 n={s9['n']:3d}/${s9['pnl']:+.0f}  "
              f"S10 n={s10['n']:3d}/${s10['pnl']:+.0f}  "
              f"({time.time()-t_w:.1f}s)")

    # Compare to 4h baseline on the same windows
    print("\n=== 4h baseline (same windows, for direct comparison) ===")
    from backtests.backtest_genetic import load_3y_candles, build_features
    data4h = load_3y_candles()
    feats4h = build_features(data4h)
    sec4h = compute_sector_features(feats4h, data4h)

    early_exit_4h = dict(
        exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
        mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
        mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
        slack_bps=DEAD_TIMEOUT_SLACK_BPS,
    )
    common_4h = dict(
        sector_features=sec4h, dxy_data=dxy_data, end_ts_ms=end_ts,
        start_capital=500.0, oi_data=oi_data, early_exit_params=early_exit_4h,
        funding_data=funding_data, interval_hours=4,
    )
    results_4h = {}
    for label, start_ts in window_specs:
        r = run_window(feats4h, data4h, start_ts_ms=start_ts, **common_4h)
        results_4h[label] = r
        s5 = r["by_strat"].get("S5", {"n": 0, "pnl": 0, "wr": 0})
        s9 = r["by_strat"].get("S9", {"n": 0, "pnl": 0, "wr": 0})
        s10 = r["by_strat"].get("S10", {"n": 0, "pnl": 0, "wr": 0})
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  "
              f"DD={r['max_dd_pct']:6.1f}%  "
              f"S5 n={s5['n']:3d}/${s5['pnl']:+.0f}  "
              f"S9 n={s9['n']:3d}/${s9['pnl']:+.0f}  "
              f"S10 n={s10['n']:3d}/${s10['pnl']:+.0f}")

    print("\n=== Δ 1h vs 4h ===")
    print(f"{'Window':<10s} {'ΔpnL%':>8s}  {'Δtrades':>8s}  {'ΔDD':>6s}  {'ΔS5pnl':>8s}  {'ΔS5n':>5s}")
    for label, _ in window_specs:
        r1, r4 = results[label], results_4h[label]
        d_pnl = r1["pnl_pct"] - r4["pnl_pct"]
        d_n = r1["n_trades"] - r4["n_trades"]
        d_dd = r1["max_dd_pct"] - r4["max_dd_pct"]
        s5_1 = r1["by_strat"].get("S5", {"n": 0, "pnl": 0})
        s5_4 = r4["by_strat"].get("S5", {"n": 0, "pnl": 0})
        d_s5_pnl = s5_1["pnl"] - s5_4["pnl"]
        d_s5_n = s5_1["n"] - s5_4["n"]
        print(f"{label:<10s} {d_pnl:>+8.1f}  {d_n:>+8d}  {d_dd:>+6.1f}  ${d_s5_pnl:>+7.0f}  {d_s5_n:>+5d}")


if __name__ == "__main__":
    main()
