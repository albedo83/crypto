"""Chantier 4 — chaîne de sorties : les deux follow-ups de l'ablation 2026-06.

B1) S10 whitelist (v11.3.4) : l'ablation la donnait quasi-PASS au retrait
    (+610/+121/+22/−2 $, ΔDD +2.4pp). Test dédié : SHORT-only CONSERVÉ,
    whitelist élargie à tout l'univers, tranches OOS 6m à dates de fin
    GLISSANTES (anti single-end-date, pattern walkforward_cut_rules_audit).

B2) dead_timeout (v11.7.2) + runner_ext (v11.7.32) : contribution marginale
    négative à l'ablation (−238/+94/−210/−231 $) — hypothèse : doublonnés par
    traj_cut/btc_drop_cut/s8_dead qui coupent plus tôt. Ablation combinée
    {−dead, −runner, −both} glissante + MATRICE DE SUBSTITUTION sur 28m :
    par quelle raison sortent les mêmes trades quand la règle est OFF, et à
    quel Δpnl — redondance réelle vs contribution.

Discipline : l'ablation détecte, le walk-forward glissant juge, AUCUN retrait
sans strict + OK utilisateur. rules.py intact (kill-switch Params, br._P).

Usage : python3 -m backtests.chantier4_exit_chain_audit
"""
from __future__ import annotations

import dataclasses as dc
import os
import sys
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS

START_CAP = 500.0
OOS_MONTHS = 6
END_OFFSETS = [0, 3, 6, 9, 12, 15, 18]   # mois de décalage de la date de fin

B1_VARIANTS = [
    ("baseline", {}),
    ("-whitelist", {"s10_allowed_tokens": frozenset(DEFAULT_PARAMS.trade_symbols)}),
]
B2_VARIANTS = [
    ("baseline", {}),
    ("-dead_timeout", {"dead_timeout_mfe_cap_bps": -99999.0}),
    ("-runner_ext", {"runner_ext_strategies": frozenset()}),
    ("-both", {"dead_timeout_mfe_cap_bps": -99999.0,
               "runner_ext_strategies": frozenset()}),
]


def run(bt, overrides, start_ms, end_ms):
    """Patch br._P (mécanisme de backtest_rule_audit) + config canonique."""
    br._P = dc.replace(DEFAULT_PARAMS, **overrides) if overrides else DEFAULT_PARAMS
    try:
        return run_window(bt["features"], bt["data"], bt["sector"], bt["dxy"],
                          start_ms, end_ms, start_capital=START_CAP,
                          oi_data=bt["oi"], funding_data=bt["funding"],
                          apply_adaptive_modulator=True, aligned=True,
                          margin_check=True, mfe_on_close=True)
    finally:
        br._P = DEFAULT_PARAMS


def main():
    print("Chargement dataset…", flush=True)
    data = load_3y_candles()
    features = build_features(data)
    sector = compute_sector_features(features, data)
    bt = {"data": data, "features": features, "sector": sector,
          "dxy": load_dxy(), "oi": load_oi(), "funding": load_funding()}
    end_max = max(c["t"] for c in data["BTC"])
    end_dt_max = datetime.fromtimestamp(end_max / 1000, tz=timezone.utc)

    summary = {}
    for title, variants in (("B1 — S10 whitelist (SHORT-only conservé)", B1_VARIANTS),
                            ("B2 — dead_timeout / runner_ext", B2_VARIANTS)):
        print(f"\n{'='*96}\n{title} — tranches OOS {OOS_MONTHS}m, fins glissantes\n{'='*96}", flush=True)
        wins = {name: 0 for name, _ in variants if name != "baseline"}
        for off in END_OFFSETS:
            end_dt = end_dt_max - relativedelta(months=off)
            start_dt = end_dt - relativedelta(months=OOS_MONTHS)
            s_ms, e_ms = int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)
            b = None
            line = f"  fin {end_dt.date()} : "
            for name, ov in variants:
                r = run(bt, ov, s_ms, e_ms)
                if name == "baseline":
                    b = (r["pnl_pct"], r["max_dd_pct"])
                    line += f"base {b[0]:+7.1f}% (DD {b[1]:5.1f}) | "
                else:
                    dpnl, ddd = r["pnl_pct"] - b[0], r["max_dd_pct"] - b[1]
                    if dpnl > 0.1:
                        wins[name] += 1
                    line += f"{name} Δ{dpnl:+6.1f}pp ΔDD{ddd:+5.1f} | "
            print(line, flush=True)
        for name, w in wins.items():
            print(f"  → retirer `{name[1:]}` GAGNE sur {w}/{len(END_OFFSETS)} tranches")
            summary[name] = w

    # B2-bis : matrice de substitution sur 28m
    print(f"\n{'='*96}\nB2-bis — substitution 28m : par quoi sortent les trades concernés ?\n{'='*96}", flush=True)
    s28 = int((end_dt_max - relativedelta(months=28)).timestamp() * 1000)
    base = run(bt, {}, s28, end_max)
    key = lambda t: (t["coin"], t["entry_t"])

    for rule, ov, base_filter in (
            ("dead_timeout", {"dead_timeout_mfe_cap_bps": -99999.0},
             lambda t: t.get("reason") == "dead_timeout"),
            ("runner_ext", {"runner_ext_strategies": frozenset()},
             lambda t: t.get("strat") == "S9")):
        alt = run(bt, ov, s28, end_max)
        alt_by_key = {key(t): t for t in alt["trades"]}
        affected = [t for t in base["trades"] if base_filter(t)]
        subs, d_pnl, n_sub = {}, 0.0, 0
        for t in affected:
            a = alt_by_key.get(key(t))
            if a is None:
                continue
            same = (a.get("reason") == t.get("reason")
                    and abs((a.get("pnl") or 0) - (t.get("pnl") or 0)) < 0.01)
            if same:
                continue
            n_sub += 1
            subs[a.get("reason")] = subs.get(a.get("reason"), 0) + 1
            d_pnl += (a.get("pnl") or 0) - (t.get("pnl") or 0)
        print(f"\n  {rule}: {len(affected)} trades base dans le périmètre, "
              f"{n_sub} réellement modifiés sans la règle")
        for rn, c in sorted(subs.items(), key=lambda kv: -kv[1]):
            print(f"    → sortent en {rn:<20} {c}")
        print(f"    Δpnl direct des modifiés (sans-règle − base) : {d_pnl:+.2f}$ (hors compounding)")
        print(f"    fenêtre 28m totale : base {base['pnl_pct']:+.1f}% vs sans-{rule} {alt['pnl_pct']:+.1f}%")


if __name__ == "__main__":
    main()
