"""Walk-forward — universe expansion test.

Hypothesis: HL has 230 perps, we trade 28. Adding new liquid tokens to
TRADE_SYMBOLS gives the bot more candidates without changing logic. Doesn't
suffer the "filter kills more winners than it saves" pattern of the 8
previous rejections — this ADDS input rather than CONSTRAINS it.

Constraint: HL public API only serves ~28m of 4h candles. New tokens have
≤ 27 months. Walk-forward limited to 12m / 6m / 3m / 1m (drop 28m).

Tokens tested (15 candidates with ≥ 12 mois of data):
  L1     : TON, ZEC, XRP, BNB, ADA
  DeFi   : HYPE, ENA, ONDO, JTO
  AI     : TAO, VIRTUAL  (new sector)
  Meme   : kPEPE, WIF, FARTCOIN

Usage:
    python3 -m backtests.backtest_universe_expand
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

from analysis.bot import config as bot_config
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MAE_FLOOR_BPS,
    DEAD_TIMEOUT_MFE_CAP_BPS, DEAD_TIMEOUT_SLACK_BPS,
)
from backtests import backtest_genetic
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_sector import compute_sector_features

CAP = 1000.0
WINDOWS = [("12m", 12), ("6m", 6), ("3m", 3), ("1m", 1)]  # no 28m for new tokens

# New tokens to test, with sector assignments
NEW_TOKENS_BY_SECTOR = {
    "L1":   ["TON", "ZEC", "XRP", "BNB", "ADA"],
    "DeFi": ["HYPE", "ENA", "ONDO", "JTO"],
    "AI":   ["TAO", "VIRTUAL"],
    "Meme": ["kPEPE", "WIF", "FARTCOIN"],
}
ALL_NEW = [t for tokens in NEW_TOKENS_BY_SECTOR.values() for t in tokens]


def setup_universe(extra_tokens_by_sector: dict[str, list[str]]):
    """Mutate bot config + backtest_genetic to expand the universe.

    Returns tuple of (saved_TOKENS, saved_SECTORS, saved_TOKEN_SECTOR) so
    the caller can restore defaults afterwards.
    """
    saved_TOKENS = list(backtest_genetic.TOKENS)
    saved_SECTORS = {k: list(v) for k, v in bot_config.SECTORS.items()}
    saved_TOKEN_SECTOR = dict(bot_config.TOKEN_SECTOR)

    for sect, toks in extra_tokens_by_sector.items():
        for t in toks:
            if t not in backtest_genetic.TOKENS:
                backtest_genetic.TOKENS.append(t)
            bot_config.SECTORS.setdefault(sect, [])
            if t not in bot_config.SECTORS[sect]:
                bot_config.SECTORS[sect].append(t)
            bot_config.TOKEN_SECTOR[t] = sect

    return saved_TOKENS, saved_SECTORS, saved_TOKEN_SECTOR


def restore_universe(saved_TOKENS, saved_SECTORS, saved_TOKEN_SECTOR):
    backtest_genetic.TOKENS[:] = saved_TOKENS
    bot_config.SECTORS.clear()
    bot_config.SECTORS.update(saved_SECTORS)
    bot_config.TOKEN_SECTOR.clear()
    bot_config.TOKEN_SECTOR.update(saved_TOKEN_SECTOR)


def fmt_row(name, deltas_pnl, deltas_dd):
    positives = sum(1 for v in deltas_pnl.values() if v > 0)
    avg_dd = sum(deltas_dd.values()) / 4
    sign = "✓" if positives == 4 and avg_dd <= 0.5 else " "
    return (f"  {sign} {name:42s}  "
            f"Δ12m={deltas_pnl['12m']:+8.1f}  Δ6m={deltas_pnl['6m']:+7.1f}  "
            f"Δ3m={deltas_pnl['3m']:+6.1f}  Δ1m={deltas_pnl['1m']:+5.1f}  "
            f"ΔDD avg={avg_dd:+5.2f}  {positives}/4")


def run_with_universe(extra_tokens_by_sector, oi_data, funding_data, dxy_data,
                      window_specs, end_ts, cap):
    """Set up an extended universe, build features, run all windows, restore."""
    saved = setup_universe(extra_tokens_by_sector)
    try:
        data = load_3y_candles()
        features = build_features(data)
        sector_features = compute_sector_features(features, data)
        early_exit = dict(
            exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
            mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
            mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
            slack_bps=DEAD_TIMEOUT_SLACK_BPS,
        )
        rs = {}
        for label, start_ts in window_specs:
            r = run_window(features, data, sector_features, dxy_data,
                           start_ts_ms=start_ts, end_ts_ms=end_ts,
                           start_capital=cap, oi_data=oi_data,
                           early_exit_params=early_exit,
                           funding_data=funding_data)
            rs[label] = r
        return rs
    finally:
        restore_universe(*saved)


def main() -> None:
    print("Loading shared data (DXY, OI, funding)...")
    dxy_data = load_dxy()
    oi_data = load_oi()
    funding_data = load_funding()

    # Use a fixed end date so windows are stable
    # First, do a baseline run to find latest ts
    saved = setup_universe({})  # no-op
    data = load_3y_candles()
    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    restore_universe(*saved)

    window_specs = [(lab, int((end_dt - relativedelta(months=m)).timestamp() * 1000))
                    for lab, m in WINDOWS]

    print(f"Data ends at {end_dt.date()}")
    print(f"Windows: {[(l, datetime.fromtimestamp(s/1000, tz=timezone.utc).date()) for l, s in window_specs]}")

    print("\n=== Baseline (28 tokens) ===")
    baseline = run_with_universe({}, oi_data, funding_data, dxy_data,
                                  window_specs, latest_ts, CAP)
    for label, _ in window_specs:
        r = baseline[label]
        s5 = r["by_strat"].get("S5", {"n":0,"pnl":0})
        s9 = r["by_strat"].get("S9", {"n":0,"pnl":0})
        s10 = r["by_strat"].get("S10", {"n":0,"pnl":0})
        s1 = r["by_strat"].get("S1", {"n":0,"pnl":0})
        print(f"  {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  "
              f"DD={r['max_dd_pct']:6.1f}%  "
              f"S1={s1['n']}/${s1['pnl']:.0f} S5={s5['n']}/${s5['pnl']:.0f} "
              f"S9={s9['n']}/${s9['pnl']:.0f} S10={s10['n']}/${s10['pnl']:.0f}")

    t0 = time.time()
    all_results: dict[str, dict] = {}

    def diff(name, rs):
        d_pnl = {l: rs[l]["pnl_pct"] - baseline[l]["pnl_pct"] for l, _ in window_specs}
        d_dd = {l: rs[l]["max_dd_pct"] - baseline[l]["max_dd_pct"] for l, _ in window_specs}
        positives = sum(1 for v in d_pnl.values() if v > 0)
        all_results[name] = {"d_pnl": d_pnl, "d_dd": d_dd, "positives": positives, "rs": rs}
        return positives, d_pnl, d_dd

    # ── (1) Individual additions ──────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(1) INDIVIDUAL — each new token added alone':^100}")
    print("=" * 100)
    for sect, toks in NEW_TOKENS_BY_SECTOR.items():
        for t in toks:
            rs = run_with_universe({sect: [t]}, oi_data, funding_data, dxy_data,
                                     window_specs, latest_ts, CAP)
            positives, d_pnl, d_dd = diff(f"+{t} ({sect})", rs)
            print(fmt_row(f"+{t} ({sect})", d_pnl, d_dd))

    # ── (2) Sector groups ─────────────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(2) SECTOR GROUPS — all tokens of a sector at once':^100}")
    print("=" * 100)
    for sect, toks in NEW_TOKENS_BY_SECTOR.items():
        rs = run_with_universe({sect: toks}, oi_data, funding_data, dxy_data,
                                 window_specs, latest_ts, CAP)
        name = f"+{sect}: {','.join(toks)}"[:50]
        positives, d_pnl, d_dd = diff(name, rs)
        print(fmt_row(name, d_pnl, d_dd))

    # ── (3) Full universe expansion ───────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'(3) FULL EXPANSION (all 14 new tokens)':^100}")
    print("=" * 100)
    rs = run_with_universe(NEW_TOKENS_BY_SECTOR, oi_data, funding_data, dxy_data,
                             window_specs, latest_ts, CAP)
    positives, d_pnl, d_dd = diff("ALL +14 new tokens", rs)
    print(fmt_row("ALL +14 new tokens", d_pnl, d_dd))
    # Per-strategy breakdown of full expansion
    print(f"\n  Per-strat breakdown of full expansion:")
    for label, _ in window_specs:
        r = rs[label]
        s5 = r["by_strat"].get("S5", {"n":0,"pnl":0})
        s9 = r["by_strat"].get("S9", {"n":0,"pnl":0})
        s10 = r["by_strat"].get("S10", {"n":0,"pnl":0})
        s1 = r["by_strat"].get("S1", {"n":0,"pnl":0})
        print(f"    {label}: pnl={r['pnl_pct']:+8.1f}%  trades={r['n_trades']:4d}  DD={r['max_dd_pct']:6.1f}%  "
              f"S1={s1['n']}/${s1['pnl']:.0f} S5={s5['n']}/${s5['pnl']:.0f} "
              f"S9={s9['n']}/${s9['pnl']:.0f} S10={s10['n']}/${s10['pnl']:.0f}")

    # ── 4/4 winners ───────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print(f"{'4/4 PnL gain & DD intact (≤ +0.5pp avg)':^100}")
    print("=" * 100)
    found = []
    for name, info in all_results.items():
        d_pnl = list(info["d_pnl"].values())
        d_dd = list(info["d_dd"].values())
        if all(p > 0 for p in d_pnl) and sum(d_dd) / 4 <= 0.5:
            found.append((name, d_pnl, d_dd))
    if not found:
        print("  (none)")
    else:
        found.sort(key=lambda x: -sum(x[1]))
        for name, d_pnl, d_dd in found[:20]:
            print(f"  {name}")
            print(f"    avg ΔPnL {sum(d_pnl)/4:+.1f}pp  avg ΔDD {sum(d_dd)/4:+.2f}pp  "
                  f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})")

    # Top 10 by sum_pnl
    print("\n" + "=" * 100)
    print(f"{'Top 10 by sum(ΔPnL) — even if not 4/4':^100}")
    print("=" * 100)
    sorted_all = sorted(all_results.items(),
                         key=lambda kv: -sum(kv[1]["d_pnl"].values()))
    for name, info in sorted_all[:15]:
        d_pnl = list(info["d_pnl"].values())
        positives = info["positives"]
        sign = "✓" if positives == 4 else " "
        print(f"  {sign} {name:42s}  sum ΔPnL={sum(d_pnl):+8.1f}  "
              f"({d_pnl[0]:+.1f}, {d_pnl[1]:+.1f}, {d_pnl[2]:+.1f}, {d_pnl[3]:+.1f})  {positives}/4")

    print(f"\nRuntime: {time.time()-t0:.0f}s ({len(all_results)} configs)")


if __name__ == "__main__":
    main()
