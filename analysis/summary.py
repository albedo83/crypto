"""Summary scorecard — aggregates key metrics from all studies.

Run: python3 -m analysis.summary
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import os

from analysis.utils import OUTPUT_DIR, apply_dark_theme


def load_csv_safe(name: str) -> pd.DataFrame:
    path = os.path.join(OUTPUT_DIR, name)
    if os.path.exists(path):
        return pd.read_csv(path)
    return pd.DataFrame()


def build_scorecard() -> pd.DataFrame:
    """Build final scorecard from study outputs."""
    rows = []

    # ── Study 01: OFI ───────────────────────────────────────────────
    ofi_spearman = load_csv_safe("ofi_spearman.csv")
    ofi_quintiles = load_csv_safe("ofi_quintiles.csv")
    ofi_hitrate = load_csv_safe("ofi_hitrate.csv")

    if not ofi_spearman.empty:
        for _, r in ofi_spearman[ofi_spearman["session"] == "all"].iterrows():
            q_row = ofi_quintiles[
                (ofi_quintiles["symbol"] == r["symbol"])
                & (ofi_quintiles["horizon"] == r["horizon"])
            ]
            edge = q_row["spread_Q5_Q1_bps"].values[0] if len(q_row) > 0 else np.nan
            # Hit rate at threshold 0.2 for this symbol/horizon
            hr = ofi_hitrate[
                (ofi_hitrate["symbol"] == r["symbol"])
                & (ofi_hitrate["horizon"] == r["horizon"])
                & (ofi_hitrate["threshold"] == 0.2)
            ]
            hit = hr["long_hit"].values[0] if len(hr) > 0 else np.nan
            rows.append({
                "signal": "OFI",
                "symbol": r["symbol"],
                "horizon": r["horizon"].replace("ret_", ""),
                "rho": r["rho"],
                "hit_rate": hit,
                "edge_bps": edge,
                "significant": "Yes" if r["pval"] < 0.05 else "No",
                "n": r["n"],
            })

    # ── Study 02: Book Imbalance ────────────────────────────────────
    book_deciles = load_csv_safe("book_imb_deciles.csv")
    if not book_deciles.empty:
        for _, r in book_deciles.iterrows():
            rows.append({
                "signal": "Book Imb (TOB)",
                "symbol": r["symbol"],
                "horizon": r["horizon"].replace("ret_", ""),
                "rho": r["rho"],
                "hit_rate": np.nan,
                "edge_bps": r["spread_bps"],
                "significant": "Yes" if r["pval"] < 0.05 else "No",
                "n": r["n"],
            })

    book_composite = load_csv_safe("book_composite.csv")
    if not book_composite.empty:
        for _, r in book_composite.iterrows():
            rows.append({
                "signal": "OFI+Book Comp",
                "symbol": r["symbol"],
                "horizon": r["horizon"].replace("ret_", ""),
                "rho": r["rho_composite"],
                "hit_rate": np.nan,
                "edge_bps": np.nan,
                "significant": "N/A",
                "n": r["n"],
            })

    # ── Study 03: Liquidation Bounce ────────────────────────────────
    liq_bounce = load_csv_safe("liq_bounce.csv")
    if not liq_bounce.empty:
        for sym in liq_bounce["symbol"].unique():
            sub = liq_bounce[liq_bounce["symbol"] == sym]
            for secs in (60, 300):
                col = f"bounce_{secs}s"
                valid = sub[col].dropna()
                if len(valid) == 0:
                    continue
                rows.append({
                    "signal": "Liq Bounce",
                    "symbol": sym,
                    "horizon": f"{secs}s",
                    "rho": np.nan,
                    "hit_rate": valid.mean(),
                    "edge_bps": sub[f"ret_{secs}s_bps"].dropna().mean(),
                    "significant": f"N<30" if len(valid) < 30 else "Yes" if valid.mean() > 0.6 else "No",
                    "n": len(valid),
                })

    # ── Study 04: ADA Session ───────────────────────────────────────
    session_quint = load_csv_safe("session_ofi_quintiles.csv")
    if not session_quint.empty:
        ada_asia = session_quint[
            (session_quint["symbol"] == "ADAUSDT")
            & (session_quint["session"] == "asian")
        ]
        for _, r in ada_asia.iterrows():
            rows.append({
                "signal": "OFI (ADA/Asia)",
                "symbol": r["symbol"],
                "horizon": r["horizon"].replace("ret_", ""),
                "rho": r["rho"],
                "hit_rate": np.nan,
                "edge_bps": r["spread_Q5_Q1_bps"],
                "significant": "Yes" if r["pval"] < 0.05 else "No",
                "n": r["n"],
            })

    if not rows:
        print("  No results found — run individual studies first")
        return pd.DataFrame()

    scorecard = pd.DataFrame(rows)
    # Sort by absolute edge
    scorecard["abs_edge"] = scorecard["edge_bps"].abs()
    scorecard = scorecard.sort_values("abs_edge", ascending=False).drop(columns=["abs_edge"])
    return scorecard


def run() -> pd.DataFrame:
    """Print and save final scorecard."""
    apply_dark_theme()
    print("=" * 70)
    print("MICROSTRUCTURE ANALYSIS — SCORECARD")
    print("=" * 70)

    scorecard = build_scorecard()
    if scorecard.empty:
        return scorecard

    # Format for display
    display = scorecard.copy()
    for col in ("rho", "hit_rate"):
        display[col] = display[col].apply(lambda v: f"{v:.4f}" if pd.notna(v) else "N/A")
    display["edge_bps"] = display["edge_bps"].apply(
        lambda v: f"{v:.2f}" if pd.notna(v) else "N/A"
    )
    display["n"] = display["n"].apply(lambda v: f"{int(v)}" if pd.notna(v) else "")

    print("\n" + display.to_string(index=False))
    scorecard.to_csv(f"{OUTPUT_DIR}/scorecard.csv", index=False)
    print(f"\n  → saved {OUTPUT_DIR}/scorecard.csv")

    # Key takeaways
    print("\n── Key Takeaways ──")
    sig = scorecard[scorecard["significant"] == "Yes"]
    if not sig.empty:
        best = sig.loc[sig["edge_bps"].abs().idxmax()]
        print(f"  Strongest signal: {best['signal']} on {best['symbol']} "
              f"({best['horizon']}) — edge {best['edge_bps']:.1f} bps, rho {best['rho']:.4f}")

    ada_asia = scorecard[scorecard["signal"] == "OFI (ADA/Asia)"]
    if not ada_asia.empty:
        r = ada_asia.iloc[0]
        print(f"  ADA/Asia OFI: rho={r['rho']:.4f}, edge={r['edge_bps']:.1f} bps "
              f"({'CONFIRMED' if r['significant'] == 'Yes' else 'NOT confirmed'})")

    liq = scorecard[scorecard["signal"] == "Liq Bounce"]
    if not liq.empty:
        r = liq.iloc[0]
        print(f"  Liquidation bounce: hit={r['hit_rate']:.1%} ({r['significant']})")

    return scorecard


if __name__ == "__main__":
    run()
