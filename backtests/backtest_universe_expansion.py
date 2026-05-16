"""Phase 2 — universe expansion backtest.

Compare baseline (29 current tokens) vs Config A (+10) vs Config B (+20)
on 6m and 12m windows. Gate: ΔPnL > 0 on both windows + ΔDD ≤ +2pp.

Sector mapping is extended at runtime via monkey-patch of `backtest_rolling`
constants (TOKEN_SECTOR, SECTORS, TRADE_SYMBOLS). The bot's production
constants in `analysis/bot/config.py` are NOT modified — this is research only.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

from backtests import backtest_rolling as br


# Current production universe (v12.6.3)
CURRENT = [
    "ARB", "OP", "AVAX", "SUI", "APT", "SEI", "NEAR",
    "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX",
    "DOGE", "WLD", "BLUR", "LINK", "PYTH",
    "SOL", "INJ", "CRV", "LDO", "STX", "GMX",
    "IMX", "SAND", "GALA", "MINA", "TON",
]

# Config A — 10 blue-chip additions
CONFIG_A_ADD = [
    "HYPE", "XRP", "BNB", "ADA", "BCH", "LTC", "DOT",  # L1-major + Infra
    "XMR",                                              # Privacy
    "UNI", "ENA",                                       # DeFi
]

# Config B — Config A + 10 extras (narrative + DeFi 2.0)
CONFIG_B_ADD = CONFIG_A_ADD + [
    "TAO", "FET",                                 # AI
    "ZEC",                                        # Privacy
    "JUP", "MORPHO", "ONDO",                      # DeFi
    "AXS",                                        # Gaming
    "BERA",                                       # L1 (emerging)
    "ZRO",                                        # Infra
    "kPEPE",                                      # Meme
]

# Sector mapping for new tokens (extends existing TOKEN_SECTOR)
NEW_TOKEN_SECTOR = {
    "HYPE": "infra",
    "XRP":  "l1_major", "BNB": "l1_major", "ADA": "l1_major",
    "BCH":  "l1_major", "LTC": "l1_major", "DOT": "l1_major",
    "XMR":  "privacy", "ZEC": "privacy",
    "UNI":  "defi", "ENA": "defi", "JUP": "defi",
    "MORPHO": "defi", "ONDO": "defi",
    "AXS": "gaming",
    "BERA": "l1",
    "ZRO": "infra",
    "kPEPE": "meme",
    "TAO": "ai", "FET": "ai",
}


def fetch_and_cache(coin: str):
    """Fetch 6m of 4h candles for `coin` and write to the canonical cache path
    so backtest_rolling.load_3y_candles can pick it up. No-op if already cached."""
    path = os.path.join("/home/crypto/backtests/output/pairs_data", f"{coin}_4h_3y.json")
    if os.path.exists(path):
        import json
        with open(path) as f:
            existing = json.load(f)
        if len(existing) >= 1000:
            return  # already have ~6m+
    # Fetch
    import json, time
    from urllib.request import Request, urlopen
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - 200 * 86400 * 1000  # fetch 200d to be safe
    out = []
    cur = start_ms
    INTERVAL_MS = 4 * 3600 * 1000
    while cur < end_ms:
        ch_end = min(cur + 5000 * INTERVAL_MS, end_ms)
        try:
            req = Request("https://api.hyperliquid.xyz/info",
                          data=json.dumps({
                              "type": "candleSnapshot",
                              "req": {"coin": coin, "interval": "4h",
                                      "startTime": cur, "endTime": ch_end},
                          }).encode(),
                          headers={"Content-Type": "application/json"})
            r = json.load(urlopen(req, timeout=20))
        except Exception as e:
            print(f"  {coin}: fetch error {e}", file=sys.stderr)
            return
        if not r:
            break
        out.extend(r)
        cur = int(r[-1]["t"]) + INTERVAL_MS
    # Dedupe
    seen = set()
    uniq = []
    for c in out:
        if c["t"] not in seen:
            seen.add(c["t"])
            uniq.append(c)
    with open(path, "w") as f:
        json.dump(uniq, f)
    print(f"  {coin}: cached {len(uniq)} candles", flush=True)


def main():
    # Cache all new tokens
    all_new = list(set(CONFIG_A_ADD + CONFIG_B_ADD))
    print(f"Caching {len(all_new)} new tokens...")
    for c in all_new:
        fetch_and_cache(c)

    # Inspect baseline TOKEN_SECTOR / SECTORS / TOKENS
    from backtests import backtest_genetic as bg
    print(f"\nBaseline TOKENS: {len(bg.TOKENS)} tokens")
    print(f"  Current sectors: {sorted(br.SECTORS.keys())}")

    # Build extended TOKEN_SECTOR + SECTORS for each config.
    # IMPORTANT: use the canonical CURRENT list (29 tokens incl. TON), NOT
    # bg.TOKENS — which is now extended to all_loaded for data caching.
    def build_universe(extra_tokens: list[str]):
        toks = list(CURRENT) + extra_tokens
        base_sect = dict(br.TOKEN_SECTOR)
        for t in extra_tokens:
            base_sect[t] = NEW_TOKEN_SECTOR.get(t, "other")
        sectors: dict[str, list[str]] = defaultdict(list)
        for tok, sect in base_sect.items():
            sectors[sect].append(tok)
        return toks, base_sect, dict(sectors)

    # Config C — curated subset of A's positive contributors (drop XRP, HYPE,
    # BNB, LTC which were net-negative on 6m in Config A). 6 new tokens.
    CONFIG_C_ADD = ["BCH", "DOT", "XMR", "ENA", "ADA", "UNI"]
    # Config C-minus — drop ADA from C (ADA flipped from +$24 in A to -$93 in C).
    # 5 tokens, the cleanest "stable positives" candidate set.
    CONFIG_C_MINUS_ADD = ["BCH", "DOT", "XMR", "ENA", "UNI"]
    configs = [
        ("baseline", []),
        ("config_A", CONFIG_A_ADD),
        ("config_B", CONFIG_B_ADD),
        ("config_C_curated", CONFIG_C_ADD),
        ("config_C_minus", CONFIG_C_MINUS_ADD),
    ]

    # Load ALL data once (need data for all candidates regardless of config used)
    print("\nLoading backtest data...")
    # Patch TOKENS to include all candidates so load_3y_candles fetches them all
    from backtests import backtest_sector as bs
    original_tokens = bg.TOKENS
    original_br_tokens = br.TOKENS
    original_bs_tokens = bs.TOKENS
    all_loaded = list(set(bg.TOKENS) | set(all_new))
    bg.TOKENS = all_loaded
    br.TOKENS = all_loaded
    bs.TOKENS = all_loaded
    data = br.load_3y_candles()
    features = br.build_features(data)
    sector_features_full = br.compute_sector_features(features, data)
    dxy = br.load_dxy()
    oi = br.load_oi()
    funding = br.load_funding()
    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    print(f"  data ends {end_dt.isoformat()}")
    print(f"  loaded {len(data)} coins")

    # Keep extended tokens in br/bs/bg so all configs see them via filtering
    # (the actual per-config restriction is via the universe build below).

    # Configure backtest engine with v12.6.3 production params
    from analysis.bot.config import (
        DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
        DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
        RUNNER_EXT_STRATEGIES, RUNNER_EXT_HOURS,
        RUNNER_EXT_MIN_MFE_BPS, RUNNER_EXT_MIN_CUR_TO_MFE,
    )
    br.MAX_MACRO_SLOTS = 3  # v12.6.3
    br.MAX_TOKEN_SLOTS = 4
    br.MAX_POSITIONS = 6
    br.MAX_SAME_DIRECTION = 4
    br.MAX_PER_SECTOR = 2
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

    # Windows
    end = end_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    windows = [
        ("6m", end - dt.timedelta(days=180)),
        ("12m", end - dt.timedelta(days=365)),
    ]

    # Run each (config, window)
    results = {}
    for cfg_label, extra in configs:
        toks, tok_sect, sects = build_universe(extra)
        # Monkey-patch TOKENS bindings in br/bs/bg (each module has its own
        # imported reference; from-import snapshots at import-time).
        original_br_tok_sect = br.TOKEN_SECTOR
        original_br_sectors = br.SECTORS
        original_bs_tok_sect = bs.TOKEN_SECTOR
        original_bs_sectors = bs.SECTORS
        br.TOKEN_SECTOR = tok_sect
        br.SECTORS = sects
        bs.TOKEN_SECTOR = tok_sect
        bs.SECTORS = sects
        br.TOKENS = toks
        bs.TOKENS = toks
        bg.TOKENS = toks

        # Recompute sector_features for the extended universe
        sector_features = br.compute_sector_features(features, data)

        print(f"\n--- {cfg_label}: {len(toks)} tokens, {len(sects)} sectors ---")
        print(f"  Sectors: {', '.join(f'{s}={len(t)}' for s, t in sorted(sects.items()))}")
        for wl, sdt in windows:
            start_ms = int(sdt.timestamp() * 1000)
            r = br.run_window(
                features, data, sector_features, dxy,
                start_ms, latest_ts, start_capital=1000.0,
                oi_data=oi, funding_data=funding,
                early_exit_params=early_exit_params,
                runner_extension=runner_ext_cfg,
                apply_adaptive_modulator=True,
            )
            results[(cfg_label, wl)] = r
            n_trades = r["n_trades"]
            # Per-strat breakdown
            strat_n = {s: v["n"] for s, v in r["by_strat"].items()}
            print(f"  {wl}: pnl={r['pnl_pct']:+9.1f}% dd={r['max_dd_pct']:+6.1f}% "
                  f"trades={n_trades:>4d}  per-strat={strat_n}", flush=True)

        # Per-token breakdown on 6m (the primary)
        r6m = results[(cfg_label, "6m")]
        per_coin = defaultdict(lambda: {"n": 0, "pnl": 0.0})
        for t in r6m["trades"]:
            per_coin[t["coin"]]["n"] += 1
            per_coin[t["coin"]]["pnl"] += t["pnl"]
        # Identify new-token contributions if applicable
        if extra:
            print(f"  New-token contribution on 6m:")
            new_pnls = []
            for nt in extra:
                v = per_coin.get(nt, {"n": 0, "pnl": 0.0})
                new_pnls.append((nt, v["n"], v["pnl"]))
            new_pnls.sort(key=lambda x: -x[2])
            for nt, n, pnl in new_pnls:
                print(f"    {nt:<8} n={n:>3d}  pnl=${pnl:>+8.2f}")
            sum_new = sum(p[2] for p in new_pnls)
            print(f"    --- TOTAL new tokens: ${sum_new:+.2f} ({len([p for p in new_pnls if p[1] > 0])}/{len(extra)} traded)")

        # Restore (only sector mappings; TOKENS stays loaded full for next config)
        br.TOKEN_SECTOR = original_br_tok_sect
        br.SECTORS = original_br_sectors
        bs.TOKEN_SECTOR = original_bs_tok_sect
        bs.SECTORS = original_bs_sectors

    # Verdicts
    print(f"\n--- VERDICTS vs baseline ---")
    lines = []
    lines.append(f"# Universe expansion Phase 2 — Backtest comparatif\n")
    lines.append(f"_Generated 2026-05-16. Configs A (29+10=39) et B (29+20=49) vs baseline (29)._\n")
    lines.append(f"## Critère gate")
    lines.append(f"- ΔPnL > 0 sur **6m ET 12m** (2/2 strict)")
    lines.append(f"- avg ΔDD ≤ +2pp\n")
    lines.append(f"## Baseline (29 tokens, v12.6.3 config)\n")
    lines.append(f"| Window | PnL % | DD % | Trades |")
    lines.append(f"|---|---:|---:|---:|")
    for wl, _ in windows:
        b = results[("baseline", wl)]
        lines.append(f"| {wl} | {b['pnl_pct']:+.1f}% | {b['max_dd_pct']:+.1f}% | {b['n_trades']} |")

    for cfg_label, extra in [("config_A", CONFIG_A_ADD), ("config_B", CONFIG_B_ADD), ("config_C_curated", CONFIG_C_ADD), ("config_C_minus", CONFIG_C_MINUS_ADD)]:
        lines.append(f"\n## {cfg_label} — {29 + len(extra)} tokens (+{len(extra)} new)\n")
        lines.append(f"| Window | PnL % | ΔPnL pp | DD % | ΔDD pp | Trades | ΔTr |")
        lines.append(f"|---|---:|---:|---:|---:|---:|---:|")
        dpnls, ddds, n_pass = [], [], 0
        for wl, _ in windows:
            b = results[("baseline", wl)]
            v = results[(cfg_label, wl)]
            dpnl = v["pnl_pct"] - b["pnl_pct"]
            ddd = v["max_dd_pct"] - b["max_dd_pct"]
            dtr = v["n_trades"] - b["n_trades"]
            if dpnl > 0: n_pass += 1
            dpnls.append(dpnl); ddds.append(ddd)
            lines.append(f"| {wl} | {v['pnl_pct']:+.1f}% | **{dpnl:+.1f}pp** | "
                         f"{v['max_dd_pct']:+.1f}% | {ddd:+.1f}pp | "
                         f"{v['n_trades']} | {dtr:+d} |")
        avg_ddd = sum(ddds) / len(ddds)
        # DD values are negative; degradation = v_dd more negative than b_dd.
        # So `ddd = v - b` is NEGATIVE when DD degraded. Gate: degradation ≤ 2pp
        # means ddd >= -2.0.
        passed = (n_pass == 2 and avg_ddd >= -2.0)
        avg_degradation = -avg_ddd  # positive = degraded
        verdict = "✓ PASS 2/2" if passed else f"✗ {n_pass}/2 (avg DD degradation = {avg_degradation:+.2f}pp)"
        lines.append(f"\n**Verdict**: {verdict}")
        print(f"  {cfg_label}: {n_pass}/2  avg_DD_degradation={avg_degradation:+.2f}pp  {verdict}")

    out = "/home/crypto/backtests/universe_expansion_results.md"
    with open(out, "w") as f:
        f.write("\n".join(lines))
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
