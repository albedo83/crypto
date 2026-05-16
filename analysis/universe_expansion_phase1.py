"""Phase 1 — universe expansion shortlist (Hyperliquid perps).

Ruthless liquidity / age / volatility cutoffs to identify candidates for
expanding TRADE_SYMBOLS beyond the current 29. No backtest yet — just the
"bouncer at the door" that filters out illiquid junk.

Strict filters:
  1. **History ≥ 180 days** (≥ 1080 4h candles). Skip new listings / memecoins
     with anomalous price history.
  2. **Volume floor**: avg daily notional ≥ MIN among current 29 tokens.
     Aucun token plus thin que le pire des 29 actuels.
  3. **Volatility band**: mean 4h range_pct in [0.5×, 2.0×] band around the
     current 29's median. Keeps S5/S10 calibration valid.

Outputs:
  - backtests/universe_candidates.md  — passing shortlist + rejection reasons
  - prints a copy-pasteable Python list for config.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.request import Request, urlopen

# Current bot universe (config.py)
CURRENT = [
    "ARB", "OP", "AVAX", "SUI", "APT", "SEI", "NEAR",
    "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX",
    "DOGE", "WLD", "BLUR", "LINK", "PYTH",
    "SOL", "INJ", "CRV", "LDO", "STX", "GMX",
    "IMX", "SAND", "GALA", "MINA", "TON",
]
REFERENCE = ["BTC", "ETH"]
HL_API = "https://api.hyperliquid.xyz/info"
DATA_DIR = "/home/crypto/backtests/output/pairs_data"
INTERVAL_MS = 4 * 3600 * 1000
MIN_HISTORY_DAYS = 180
MIN_CANDLES = 1050  # ≥ ~175 days (180d × 6/day = 1080 but allow 5-day buffer for fetch boundary)


def post(body: dict) -> object:
    req = Request(HL_API, data=json.dumps(body).encode(),
                  headers={"Content-Type": "application/json"})
    return json.load(urlopen(req, timeout=20))


def fetch_universe() -> list[str]:
    """Get the list of all perp coins available on Hyperliquid."""
    r = post({"type": "metaAndAssetCtxs"})
    universe = r[0]["universe"]
    return [u["name"] for u in universe]


def fetch_candles(coin: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch 4h candles for `coin`. HL caps at 5000 per request."""
    out = []
    cur = start_ms
    while cur < end_ms:
        chunk_end = min(cur + 5000 * INTERVAL_MS, end_ms)
        try:
            r = post({
                "type": "candleSnapshot",
                "req": {"coin": coin, "interval": "4h",
                        "startTime": cur, "endTime": chunk_end},
            })
        except Exception as e:
            print(f"  {coin}: fetch error {e}", file=sys.stderr)
            return out
        if not r:
            break
        out.extend(r)
        # Move forward by the last candle's open time + 1 interval to avoid dup
        last_t = int(r[-1]["t"])
        cur = last_t + INTERVAL_MS
    # Dedupe by `t`
    seen = set()
    uniq = []
    for c in out:
        if c["t"] not in seen:
            seen.add(c["t"])
            uniq.append(c)
    return uniq


def cached_or_fetch(coin: str, start_ms: int, end_ms: int) -> list[dict]:
    """Use the cached *_4h_3y.json if it exists (current 29). Otherwise
    fetch fresh from HL. We don't write to cache for new candidates — those
    only get cached if they pass and we decide to add them."""
    path = os.path.join(DATA_DIR, f"{coin}_4h_3y.json")
    if os.path.exists(path):
        with open(path) as f:
            cs = json.load(f)
        return [c for c in cs if start_ms <= c["t"] <= end_ms]
    # Not cached — fetch fresh
    print(f"  {coin}: fetching from HL...", file=sys.stderr)
    return fetch_candles(coin, start_ms, end_ms)


def compute_stats(candles: list[dict]) -> dict | None:
    """Returns dict with avg_daily_notional, mean_range_pct, n_candles,
    age_days, or None if data is insufficient."""
    if len(candles) < MIN_CANDLES:
        return {"n_candles": len(candles), "insufficient": True}
    # avg daily notional volume = sum(v_i × close_i) / N_candles × 6
    # (6 candles per day)
    notional = sum(float(c["v"]) * float(c["c"]) for c in candles)
    total_candles = len(candles)
    avg_daily_notional = notional / total_candles * 6
    # range_pct = (high - low) / close in bps, per candle
    ranges = []
    for c in candles:
        h = float(c["h"]); lo = float(c["l"]); cl = float(c["c"])
        if cl > 0:
            ranges.append((h - lo) / cl * 1e4)
    mean_range_pct = sum(ranges) / len(ranges) if ranges else 0
    # Sort for percentile stats
    ranges.sort()
    p50 = ranges[len(ranges) // 2] if ranges else 0
    age_days = total_candles / 6
    first_ts = candles[0]["t"]
    last_ts = candles[-1]["t"]
    return {
        "n_candles": total_candles,
        "age_days": round(age_days, 1),
        "avg_daily_notional": round(avg_daily_notional, 0),
        "mean_range_pct": round(mean_range_pct, 1),
        "median_range_pct": round(p50, 1),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "insufficient": False,
    }


def main():
    # 6-month window
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - 180 * 86400 * 1000

    print(f"Fetching HL universe...")
    universe = fetch_universe()
    print(f"  HL has {len(universe)} perpetuals listed.")

    # Compute stats for current 29 tokens (cached)
    print(f"\nComputing baselines from current {len(CURRENT)} tokens (cached)...")
    cur_stats = {}
    for coin in CURRENT:
        candles = cached_or_fetch(coin, start_ms, end_ms)
        s = compute_stats(candles)
        if s and not s.get("insufficient"):
            cur_stats[coin] = s
        else:
            print(f"  {coin}: insufficient cached data ({s})")

    # Baseline floors
    vols = [s["avg_daily_notional"] for s in cur_stats.values()]
    ranges = [s["mean_range_pct"] for s in cur_stats.values()]
    median_vol = sorted(vols)[len(vols) // 2]
    min_vol_baseline = min(vols)
    median_range = sorted(ranges)[len(ranges) // 2]
    print(f"\nCurrent universe baselines (over last 6m):")
    print(f"  median daily volume    = ${median_vol:>15,.0f}")
    print(f"  min daily volume       = ${min_vol_baseline:>15,.0f}   ← strict floor")
    print(f"  median range_pct (bps) = {median_range:>15.1f}")
    print(f"  range allowed band     = [{median_range*0.5:.1f}, {median_range*2.0:.1f}] bps")

    # Show current 29 worst-on-volume to sanity-check the floor
    cur_sorted = sorted(cur_stats.items(), key=lambda x: x[1]["avg_daily_notional"])
    print(f"\n  Current 29 sorted by volume (thinnest 5):")
    for c, s in cur_sorted[:5]:
        print(f"    {c:<6} vol=${s['avg_daily_notional']:>13,.0f}  range={s['mean_range_pct']:.1f}bps")

    # Candidates: HL universe minus current + reference
    skip = set(CURRENT) | set(REFERENCE)
    candidates = [c for c in universe if c not in skip]
    print(f"\nCandidates to evaluate (universe \\ current \\ ref): {len(candidates)}")

    # Fetch + score each
    passing = []
    rejected = []
    for i, coin in enumerate(candidates):
        if i % 20 == 0:
            print(f"  [{i+1}/{len(candidates)}] {coin}...", flush=True)
        try:
            candles = fetch_candles(coin, start_ms, end_ms)
        except Exception as e:
            rejected.append((coin, None, f"fetch_error: {e}"))
            continue
        s = compute_stats(candles)
        if s is None or s.get("insufficient"):
            n_c = s["n_candles"] if s else 0
            rejected.append((coin, s, f"insufficient_history ({n_c} candles, need {MIN_CANDLES})"))
            continue
        # Apply strict filters
        if s["avg_daily_notional"] < min_vol_baseline:
            rejected.append((coin, s, f"volume too thin (${s['avg_daily_notional']:,.0f} < ${min_vol_baseline:,.0f})"))
            continue
        if not (median_range * 0.5 <= s["mean_range_pct"] <= median_range * 2.0):
            band_msg = f"range {s['mean_range_pct']:.1f} outside [{median_range*0.5:.1f}, {median_range*2.0:.1f}]"
            rejected.append((coin, s, band_msg))
            continue
        passing.append((coin, s))
        # Brief rate-limit
        time.sleep(0.05)

    # Sort passing by volume
    passing.sort(key=lambda x: -x[1]["avg_daily_notional"])

    print(f"\n{'='*70}")
    print(f"  SHORTLIST: {len(passing)} candidates pass all filters")
    print(f"{'='*70}")
    print(f"\n  {'Token':<8} {'Vol/day $':>15} {'Range bps':>10} {'Age (d)':>9}")
    for coin, s in passing:
        print(f"  {coin:<8} {s['avg_daily_notional']:>15,.0f} {s['mean_range_pct']:>10.1f} {s['age_days']:>9.1f}")

    # Write report
    lines = []
    lines.append(f"# Universe expansion — Phase 1 shortlist\n")
    lines.append(f"_Generated 2026-05-16. HL universe scan, 6-month window._\n")
    lines.append(f"## Strict filters")
    lines.append(f"1. History ≥ 180d ({MIN_CANDLES} 4h candles)")
    lines.append(f"2. Avg daily volume ≥ ${min_vol_baseline:,.0f} (= min of current 29)")
    lines.append(f"3. Mean range_pct ∈ [{median_range*0.5:.1f}, {median_range*2.0:.1f}] bps ({0.5}×–{2.0}× current median)\n")
    lines.append(f"## Current universe baseline (6m)")
    lines.append(f"- 29 tokens scanned")
    lines.append(f"- Min daily volume: ${min_vol_baseline:,.0f}")
    lines.append(f"- Median daily volume: ${median_vol:,.0f}")
    lines.append(f"- Median range_pct: {median_range:.1f} bps")
    thin = ", ".join(f"{c}=${s['avg_daily_notional']:,.0f}" for c, s in cur_sorted[:5])
    lines.append(f"- Thinnest 5 (volume): {thin}\n")
    lines.append(f"## ✓ Shortlist ({len(passing)} candidates pass)\n")
    lines.append("| Token | Avg daily vol ($) | Mean range (bps) | Age (days) |")
    lines.append("|---|---:|---:|---:|")
    for coin, s in passing:
        lines.append(f"| {coin} | {s['avg_daily_notional']:>,.0f} | {s['mean_range_pct']:.1f} | {s['age_days']:.0f} |")

    lines.append(f"\n## ✗ Rejected ({len(rejected)} candidates)\n")
    # Group by rejection reason
    by_reason = {}
    for coin, s, reason in rejected:
        # Short reason key
        if "insufficient_history" in reason:
            key = "insufficient_history"
        elif "volume too thin" in reason:
            key = "volume too thin"
        elif "range" in reason:
            key = "volatility out of band"
        elif "fetch_error" in reason:
            key = "fetch_error"
        else:
            key = "other"
        by_reason.setdefault(key, []).append((coin, s, reason))
    for key, items in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        lines.append(f"### {key} ({len(items)} tokens)")
        for coin, s, reason in items[:20]:
            if s:
                lines.append(f"- `{coin}` — vol=${s.get('avg_daily_notional',0):,.0f} range={s.get('mean_range_pct',0):.1f} age={s.get('age_days',0):.0f}d → {reason}")
            else:
                lines.append(f"- `{coin}` → {reason}")
        if len(items) > 20:
            lines.append(f"- ... and {len(items)-20} more")
        lines.append("")

    # Copy-pasteable Python list
    lines.append(f"\n## Python list for `TRADE_SYMBOLS` (if shipping)\n```python")
    lines.append("# Candidates passing Phase 1 strict filters — Phase 2 backtest required")
    lines.append(f"NEW_CANDIDATES = {[c for c, _ in passing]!r}")
    lines.append("```\n")

    out = "/home/crypto/backtests/universe_candidates.md"
    with open(out, "w") as f:
        f.write("\n".join(lines))
    print(f"\nReport: {out}")
    print(f"\nNext: run Phase 2 backtest (6m + 12m) comparing baseline 29 vs 29 + N candidates.")


if __name__ == "__main__":
    main()
