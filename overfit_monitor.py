#!/usr/bin/env python3
r"""Monitor d'overfit — décompose objectivement la « déception » live-vs-BT.

Pour la fenêtre réellement vécue d'un bot, sépare la déception en deux termes
structurels (cf. travail de fourmi 2026-06-30) :

  Promesse(IS)  →  OOS-BT  →  Live
       \_______________/      \________/
        overfit + régime       exécution (sorties + sélection)

- Promesse(IS)   = rendement médian du BT sur des fenêtres de MÊME durée tirées
                   de la période de développement (28 m, AVANT le déploiement du bot).
- OOS-BT         = le BT (moteur idéalisé) rejoué sur la fenêtre RÉELLE du bot
                   (out-of-sample aux règles). Situé en percentile de la distrib IS.
- Live           = equity réalisée du bot.
- overfit+régime = Promesse − OOS-BT  (BT-vs-BT, sans bruit d'exécution ; la part
                   PERSISTANTE sur plusieurs fenêtres = l'overfit, la part régime oscille).
- exécution      = OOS-BT − Live, décomposé sorties (matched same-reason) /
                   trajectoire (matched diff-reason) / sélection (live-only − BT-only).

READ-ONLY : lit bot.db + state.json + données backtest, écrit UNIQUEMENT un event
`OVERFIT_MONITOR` dans la table events (sauf --dry-run). Ne touche ni rules.py ni le bot.

Usage :
    python3 overfit_monitor.py --bot paper --dry-run     # stdout, rien d'écrit
    python3 overfit_monitor.py --bot live --no-telegram  # logge l'event, pas de TG
    python3 overfit_monitor.py --bot live
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

IS_MONTHS = 28  # période de développement (les règles ont été calées dessus)


def _resolve_window(bot_key, db_path, state_path):
    """start_dt = max(deploy, perf_track_start_ts, 1er trade) — même logique que btlive."""
    from backtests.backtest_rolling import BOT_DEPLOYMENTS
    deploy_map = {b: d for b, d in BOT_DEPLOYMENTS}
    if bot_key not in deploy_map:
        sys.exit(f"Pas de date de déploiement pour {bot_key}")
    start_dt = dt.datetime.fromisoformat(deploy_map[bot_key]).replace(tzinfo=dt.timezone.utc)
    if os.path.exists(state_path):
        try:
            perf_ts = float(json.load(open(state_path)).get("_perf_track_start_ts", 0) or 0)
            if perf_ts > 0:
                d = dt.datetime.fromtimestamp(perf_ts, dt.timezone.utc)
                start_dt = max(start_dt, d)
        except Exception:
            pass
    try:
        row = sqlite3.connect(db_path).execute("SELECT MIN(entry_time) FROM trades").fetchone()
        if row and row[0]:
            start_dt = max(start_dt, dt.datetime.fromisoformat(row[0].replace('Z', '+00:00')))
    except Exception:
        pass
    return start_dt


def _is_distribution(trades_28m, start_cap, win_days, cutoff_ms):
    """Rendements glissants win_days j sur l'equity 28m, fenêtres se terminant AVANT
    le déploiement (cutoff_ms) = vraie distribution in-sample. Renvoie (médiane, p_oos_fn)."""
    pts = {}
    cap = start_cap
    for t in sorted(trades_28m, key=lambda x: x["exit_t"]):
        cap += t["pnl"]
        day = dt.datetime.fromtimestamp(t["exit_t"] / 1000, dt.timezone.utc).date()
        pts[day] = cap
    days = sorted(pts)
    if len(days) < 5:
        return None, None
    eqs = np.array([pts[d] for d in days])
    darr = np.array([dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc) for d in days])
    cutoff = dt.datetime.fromtimestamp(cutoff_ms / 1000, dt.timezone.utc)
    rolls = []
    for i in range(len(days)):
        if darr[i] >= cutoff:
            break  # fenêtres IS = celles qui finissent avant le déploiement
        mask = darr <= darr[i] - dt.timedelta(days=win_days)
        if not mask.any():
            continue
        j = np.where(mask)[0][-1]
        if eqs[j] > 0:
            rolls.append((eqs[i] / eqs[j] - 1) * 100)
    if not rolls:
        return None, None
    rr = np.array(rolls)
    return float(np.median(rr)), (lambda v: float((rr <= v).mean() * 100))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default="live", choices=["live", "paper", "junior"])
    ap.add_argument("--dry-run", action="store_true", help="stdout seulement, n'écrit pas l'event")
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    from analysis.btlive_compare import (
        BOTS, read_live_trades, match_trades, equity_curve)
    from backtests import backtest_rolling as br

    out_rel, bot_key, defcap = BOTS[args.bot]
    out_dir = os.path.join(ROOT, out_rel)
    db_path = os.path.join(out_dir, "bot.db")
    state_path = os.path.join(out_dir, "state.json")
    if not os.path.exists(db_path):
        sys.exit(f"DB introuvable: {db_path}")

    start_dt = _resolve_window(bot_key, db_path, state_path)
    start_ms = int(start_dt.timestamp() * 1000)
    start_cap = float(defcap)

    print("=" * 64)
    print(f"  OVERFIT MONITOR — {bot_key.upper()}")
    print("=" * 64)
    print(f"  Fenêtre : depuis {start_dt:%Y-%m-%d} | capital ${start_cap:.0f}", flush=True)

    # ── données (chargées une fois) ──
    print("  Chargement données…", flush=True)
    data = br.load_3y_candles()
    feats = br.build_features(data)
    sectors = br.compute_sector_features(feats, data)
    dxy, oi, fund = br.load_dxy(), br.load_oi(), br.load_funding()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = dt.datetime.fromtimestamp(end_ms / 1000, dt.timezone.utc)
    win_days = max(1, (end_dt - start_dt).days)

    cfg = dict(start_capital=start_cap, oi_data=oi, funding_data=fund,
               apply_adaptive_modulator=True, aligned=True, margin_check=True,
               mfe_on_close=True)

    # ── OOS-BT : BT sur la fenêtre réelle du bot ──
    print("  Run OOS-BT (fenêtre live)…", flush=True)
    oos = br.run_window(feats, data, sectors, dxy, start_ms, end_ms, **cfg)
    oos_pct = oos["pnl_pct"]
    bt_trades = [{"coin": t["coin"], "strat": t["strat"], "dir": t["dir"],
                  "entry_ts": int(t["entry_t"]), "exit_ts": int(t["exit_t"]),
                  "pnl": t.get("pnl", 0.0), "reason": t.get("reason", ""),
                  "matched_to": None} for t in oos["trades"]]

    # ── distribution IS (28m, fenêtres finissant avant le déploiement) ──
    print("  Run 28m (distribution in-sample)…", flush=True)
    is_start = int((end_dt - dt.timedelta(days=int(IS_MONTHS * 30.4))).timestamp() * 1000)
    is_run = br.run_window(feats, data, sectors, dxy, is_start, end_ms, **cfg)
    promise, pctile_fn = _is_distribution(is_run["trades"], start_cap, win_days, start_ms)
    pctile = pctile_fn(oos_pct) if pctile_fn else None

    # ── Live réel ──
    live = [t for t in read_live_trades(db_path) if t["entry_ts"] >= start_ms]
    if not live:
        sys.exit("Aucun trade live dans la fenêtre.")
    _, live_final = equity_curve(live, start_cap)
    live_pct = (live_final / start_cap - 1) * 100

    # ── décomposition exécution (OOS-BT → Live), en $ ──
    pairs = match_trades(live, bt_trades)
    same = diff = 0.0
    for li, bi in pairs:
        d = live[li]["pnl"] - bt_trades[bi]["pnl"]
        if live[li]["reason"] == bt_trades[bi]["reason"]:
            same += d
        else:
            diff += d
    live_only = sum(t["pnl"] for t in live if t["matched_to"] is None)
    bt_only = sum(b["pnl"] for b in bt_trades if b["matched_to"] is None)
    exec_gap = (live_pct - oos_pct) * start_cap / 100  # $ live − OOS-BT (négatif = live sous le BT)

    # ── termes ──
    overfit_regime = (promise - oos_pct) if promise is not None else None

    print()
    pr = f"{promise:+.1f}%" if promise is not None else "n/a"
    pc = f"p{pctile:.0f}" if pctile is not None else "n/a"
    print(f"  Promesse(IS médiane {win_days}j) {pr}  →  OOS-BT {oos_pct:+.1f}% ({pc} de l'IS)  →  Live {live_pct:+.1f}%")
    if overfit_regime is not None:
        print(f"  • overfit + régime (Promesse − OOS-BT) = {overfit_regime:+.1f}pp"
              + ("   [OOS-BT dans l'enveloppe IS]" if pctile and pctile > 5 else
                 "   [OOS-BT SOUS le p5 IS = hors-échantillon/overfit]" if pctile is not None else ""))
    print(f"  • exécution (Live − OOS-BT) = {exec_gap:+.0f}$  "
          f"[sorties {same:+.0f}$ · trajectoire {diff:+.0f}$ · sélection {live_only - bt_only:+.0f}$]")
    print(f"    (matched {len(pairs)} · live-only ${live_only:+.0f} · BT-only ${bt_only:+.0f})")

    payload = {
        "bot": bot_key, "window_start": start_dt.date().isoformat(), "win_days": win_days,
        "start_cap": round(start_cap, 2),
        "promise_is_pct": round(promise, 2) if promise is not None else None,
        "oos_bt_pct": round(oos_pct, 2), "oos_pctile_in_is": round(pctile, 1) if pctile is not None else None,
        "live_pct": round(live_pct, 2),
        "overfit_regime_pp": round(overfit_regime, 2) if overfit_regime is not None else None,
        "exec_gap_usd": round(exec_gap, 2),
        "exec_exits_usd": round(same, 2), "exec_traj_usd": round(diff, 2),
        "exec_selection_usd": round(live_only - bt_only, 2),
        "n_matched": len(pairs), "n_live": len(live), "n_bt": len(bt_trades),
    }

    if args.dry_run:
        print("\n  [--dry-run] event NON écrit. Payload :")
        print("  " + json.dumps(payload, ensure_ascii=False))
        return 0

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO events (ts, event, symbol, data) VALUES (?,?,?,?)",
                     (int(time.time()), "OVERFIT_MONITOR", "", json.dumps(payload, default=str)))
        conn.commit(); conn.close()
        print("\n  event OVERFIT_MONITOR écrit.")
    except Exception as e:
        print(f"\n  [warn] écriture event échouée: {e}")

    if not args.no_telegram:
        try:
            import ai_notify
            msg = (f"[Overfit {bot_key}] {win_days}j depuis {start_dt:%m-%d}\n"
                   f"Promesse {pr} → OOS-BT {oos_pct:+.1f}% ({pc}) → Live {live_pct:+.1f}%\n"
                   f"overfit+régime {overfit_regime:+.1f}pp · exécution {exec_gap:+.0f}$")
            ai_notify.send_telegram(msg, source="overfit_monitor")
        except Exception as e:
            print(f"  [warn] Telegram: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
