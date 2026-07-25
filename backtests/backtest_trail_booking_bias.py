"""Quantifie le BIAIS DE BOOKING DES TRAILS dans le backtest.

Constat live (2026-07-25, LDO) : quand le prix traverse le niveau du trail À
L'INTÉRIEUR d'une bougie, le BT/paper bookent le NIVEAU du trail (_synth) alors
que le live exécute un ordre marché à la clôture suivante (prix réel, parfois
très loin). Mesuré sur 23 trades SENIOR-vs-paper : sorties au mark (timeout,
n=18) → écart +14 bps (bruit) ; sorties synthétiques (prop_trail, n=5) →
écart −115 bps (systématique).

Ici : on rejoue le BT en booking RÉALISTE (les trails sortent à la clôture,
comme le live) et on mesure ce que le BT perd. Aucune modif de fichier prod —
monkeypatch local de rules.evaluate_exit dans le namespace de backtest_rolling.

Le catastrophe_stop N'EST PAS touché : c'est un trigger reduce-only résident
sur l'exchange, il s'exécute réellement près de son niveau.
"""
from __future__ import annotations
import sys, json
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
sys.path.insert(0, "/home/crypto")
import backtests.backtest_rolling as R
from backtests.backtest_genetic import build_features, load_3y_candles
from backtests.backtest_sector import compute_sector_features
from alfred import rules as _r

# Règles dont le prix synthétique est IRRÉALISTE depuis v1.8.0 (évaluées
# seulement aux clôtures 4h → le marché peut avoir gappé au travers).
TRAIL_REASONS = {"prop_trail", "s10_trailing", "s8_inlife", "opp_floor"}

print("Loading data...", flush=True)
data = load_3y_candles(); features = build_features(data)
sf = compute_sector_features(features, data)
dxy = R.load_dxy(); oi = R.load_oi(); fu = R.load_funding()
latest = max(c["t"] for c in data["BTC"])
end_dt = datetime.fromtimestamp(latest/1000, tz=timezone.utc)
PROD = dict(sector_features=sf, dxy_data=dxy, start_capital=1000.0, oi_data=oi,
            funding_data=fu, apply_adaptive_modulator=True, aligned=True,
            margin_check=True, mfe_on_close=True)

_ORIG = _r.evaluate_exit

def _realistic(pos, ur, m, p, *, worst_bps=None, trail_gate=True):
    """evaluate_exit mais les trails sortent au MARK (exit_price=None) au lieu
    de leur niveau synthétique — sémantique réelle du live."""
    d = _ORIG(pos, ur, m, p, worst_bps=worst_bps, trail_gate=trail_gate)
    if d is not None and d.action == "exit" and d.reason in TRAIL_REASONS:
        return _r.ExitDecision(d.action, d.reason, None, d.extend_hours)
    return d

def run(a, b, realistic):
    R._rules.evaluate_exit = _realistic if realistic else _ORIG
    try:
        return R.run_window(features, data, start_ts_ms=a, end_ts_ms=b, **PROD)
    finally:
        R._rules.evaluate_exit = _ORIG

print("\n" + "="*86)
print("BIAIS DE BOOKING DES TRAILS — BT optimiste (synthétique) vs réaliste (au mark)")
print("="*86)
print(f"  règles concernées : {', '.join(sorted(TRAIL_REASONS))}")
print(f"  {'fenêtre':26} {'BT actuel':>12} {'BT réaliste':>12} {'Δ pnl':>10} {'Δ DD':>9}  n_trail")

rows = []
wins = [("28m", None)] + [(f"OOS {o}-{o+6}m", o) for o in (0, 6, 12, 18)]
first = min(c["t"] for c in data["BTC"])
for lbl, off in wins:
    if off is None:
        a, b = first, latest
    else:
        oe = end_dt - relativedelta(months=off); os_ = oe - relativedelta(months=6)
        a, b = int(os_.timestamp()*1000), int(oe.timestamp()*1000)
    rb = run(a, b, False); rv = run(a, b, True)
    n_trail = sum(1 for t in rb["trades"] if t.get("reason") in TRAIL_REASONS)
    dp = rv["pnl_pct"] - rb["pnl_pct"]; dd = rv["max_dd_pct"] - rb["max_dd_pct"]
    rel = (rv["pnl"]/rb["pnl"]-1)*100 if rb["pnl"] else 0
    rows.append(dict(label=lbl, base_pct=rb["pnl_pct"], real_pct=rv["pnl_pct"],
                     dpnl=dp, ddd=dd, n_trail=n_trail, rel=rel,
                     base_pnl=rb["pnl"], real_pnl=rv["pnl"]))
    print(f"  {lbl:26} {rb['pnl_pct']:+11.1f}% {rv['pnl_pct']:+11.1f}% {dp:+9.1f}pp {dd:+8.1f}pp  {n_trail}")

print("\n  → part du P&L du BT qui vient du booking optimiste des trails :")
for r in rows:
    print(f"     {r['label']:26} {r['rel']:+7.1f}%  du P&L (BT ${r['base_pnl']:+,.0f} → réaliste ${r['real_pnl']:+,.0f})")

import os
json.dump(rows, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"trail_bias.json"),"w"), indent=1)
print("\nsaved trail_bias.json")
