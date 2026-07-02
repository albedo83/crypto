"""Chantier 3 — coûts réels par signal vs hypothèses du backtest.

(a) Slippage d'entrée réel = avgPx d'entrée vs close de la bougie 4h du signal
    (le BT aligné entre au close de la bougie signal). Ventilé par stratégie,
    par bot (live/junior/baby = fills réels ; paper = drift pur sans exécution).
(b) Funding réellement payé par trade vs le flat 1 bps du modèle bot ET vs
    l'intégrale historique du BT (compute_funding_cost).

Sémantique funding_usdt :
  - Alfred (bots/*/bot.db) : ajustement = réel + size×1bps/1e4 → réel = stored − size×1e-4
  - Legacy (reversal_ticks.db) : funding réel signé directement (v11.7.5)
Convention : cost_bps = −réel/size×1e4 (positif = payé).

Usage : python3 -m backtests.measure_costs_by_signal
"""
import sqlite3, sys
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, "/home/crypto")

MARKET_DB = "/home/crypto/alfred/data/market.db"
H4 = 14_400  # secondes

BOTS = [
    ("live",   "/home/crypto/alfred/data/bots/live/bot.db",   "alfred", True),
    ("junior", "/home/crypto/alfred/data/bots/junior/bot.db", "alfred", True),
    ("baby",   "/home/crypto/alfred/data/bots/baby/bot.db",   "alfred", True),
    ("paper",  "/home/crypto/alfred/data/bots/paper/bot.db",  "alfred", False),
    ("legacy", "/home/crypto/analysis/output_live/reversal_ticks.db", "legacy", True),
]
LEGACY_MIN_ENTRY = "2026-05-27"   # v12.9.0 : entrées alignées au close 4h


def load_candle_closes():
    """{(symbol, t_open_s): close} depuis le store canonique."""
    db = sqlite3.connect(MARKET_DB)
    out = {}
    for sym, t, c in db.execute(
            "SELECT symbol, t, c FROM candles WHERE interval='4h' AND closed=1"):
        out[(sym, int(t // 1000))] = c
    db.close()
    return out


def parse_dir(v):
    return 1 if str(v) in ("1", "LONG") else -1


def collect():
    closes = load_candle_closes()
    rows = []
    for bot, path, schema, real_fills in BOTS:
        try:
            db = sqlite3.connect(path); db.row_factory = sqlite3.Row
            q = ("SELECT symbol, direction, strategy, entry_time, entry_price, "
                 "exit_time, hold_hours, size_usdt, funding_usdt, net_bps, "
                 "pnl_usdt, reason FROM trades")
            if schema == "legacy":
                q += f" WHERE entry_time >= '{LEGACY_MIN_ENTRY}'"
            trades = db.execute(q).fetchall()
            db.close()
        except Exception as e:
            print(f"[{bot}] lecture impossible: {e}", file=sys.stderr)
            continue
        for t in trades:
            dt = datetime.fromisoformat(t["entry_time"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ets = dt.timestamp()
            boundary = int(ets // H4) * H4
            if ets - boundary > 600:       # entrée non alignée (manuelle…)
                continue
            c_sig = closes.get((t["symbol"], boundary - H4))
            if not c_sig or not t["entry_price"]:
                continue
            d = parse_dir(t["direction"])
            slip = d * (t["entry_price"] / c_sig - 1) * 1e4   # + = adverse
            size = t["size_usdt"] or 0.0
            f_real_usd = None
            if real_fills and t["funding_usdt"] is not None and size > 0:
                stored = t["funding_usdt"]
                f_real_usd = (stored - size * 1e-4) if schema == "alfred" else stored
            rows.append({
                "bot": bot, "sym": t["symbol"], "strat": t["strategy"], "dir": d,
                "entry_ts": ets, "hold_h": t["hold_hours"] or 0.0, "size": size,
                "slip_bps": slip,
                "fund_cost_bps": (-f_real_usd / size * 1e4) if f_real_usd is not None else None,
                "real_fills": real_fills,
            })
    return rows


def stats(vals):
    if not vals:
        return "n=0"
    v = sorted(vals)
    n = len(v)
    mean = sum(v) / n
    med = v[n // 2]
    p90 = v[min(n - 1, int(n * 0.9))]
    return f"n={n:<3} mean={mean:+7.1f} med={med:+7.1f} p90={p90:+7.1f}"


def main():
    rows = collect()
    strats = sorted({r["strat"] for r in rows})

    print("=" * 100)
    print("(a) SLIPPAGE D'ENTRÉE (bps, + = adverse) — avgPx vs close bougie signal")
    print(f"    Modèle BT : BACKTEST_SLIPPAGE_BPS = 4.0 (round-trip, donc ~2/côté)")
    print("=" * 100)
    for kind, label in ((True, "FILLS RÉELS (live+junior+baby+legacy)"),
                        (False, "PAPER (drift pur, pas d'exécution)")):
        print(f"\n  {label}")
        for s in strats:
            vals = [r["slip_bps"] for r in rows if r["strat"] == s and r["real_fills"] == kind]
            print(f"    {s:<4} {stats(vals)}")
        allv = [r["slip_bps"] for r in rows if r["real_fills"] == kind]
        print(f"    ALL  {stats(allv)}")
    # par bot (fills réels)
    print("\n  Par bot (fills réels, toutes stratégies) :")
    for b in ("live", "junior", "baby", "legacy"):
        vals = [r["slip_bps"] for r in rows if r["bot"] == b]
        print(f"    {b:<7} {stats(vals)}")

    print()
    print("=" * 100)
    print("(b) FUNDING RÉEL PAR TRADE (bps du notionnel, + = payé)")
    print("    Modèle bot : 1 bps flat / trade · Modèle BT : intégrale historique horaire")
    print("=" * 100)
    fr = [r for r in rows if r["fund_cost_bps"] is not None]
    print("\n  Par stratégie :")
    for s in strats:
        vals = [r["fund_cost_bps"] for r in fr if r["strat"] == s]
        hh = [r["hold_h"] for r in fr if r["strat"] == s]
        per_h = (sum(vals) / sum(hh)) if hh and sum(hh) > 0 else 0
        print(f"    {s:<4} {stats(vals)}  | hold moyen {sum(hh)/len(hh) if hh else 0:5.1f}h "
              f"→ {per_h:+.2f} bps/h")
    print("\n  Par stratégie × direction :")
    for s in strats:
        for d, dl in ((1, "LONG"), (-1, "SHORT")):
            vals = [r["fund_cost_bps"] for r in fr if r["strat"] == s and r["dir"] == d]
            if vals:
                print(f"    {s:<4} {dl:<5} {stats(vals)}")
    print("\n  Par bucket de hold :")
    for lo, hi in ((0, 12), (12, 24), (24, 48), (48, 999)):
        vals = [r["fund_cost_bps"] for r in fr if lo <= r["hold_h"] < hi]
        print(f"    {lo:>2}-{hi if hi < 999 else '∞':<3}h {stats(vals)}")

    # (b-bis) validation de l'intégrale BT sur les mêmes trades
    try:
        from backtests.backtest_rolling import load_funding, compute_funding_cost
        fd = load_funding()
        diffs = []
        by_strat = defaultdict(list)
        for r in fr:
            bt_usd = compute_funding_cost(
                fd, r["sym"], r["dir"], int(r["entry_ts"] * 1000),
                int((r["entry_ts"] + r["hold_h"] * 3600) * 1000), r["size"])
            bt_bps = bt_usd / r["size"] * 1e4 if r["size"] else 0.0
            diffs.append(bt_bps - r["fund_cost_bps"])
            by_strat[r["strat"]].append((r["fund_cost_bps"], bt_bps))
        print("\n  Intégrale BT vs réel (Δ bps, + = BT surestime le coût) :")
        print(f"    ALL  {stats(diffs)}")
        for s in strats:
            pairs = by_strat.get(s, [])
            if pairs:
                real_m = sum(p[0] for p in pairs) / len(pairs)
                bt_m = sum(p[1] for p in pairs) / len(pairs)
                print(f"    {s:<4} réel moyen {real_m:+6.1f} vs BT {bt_m:+6.1f} (n={len(pairs)})")
    except Exception as e:
        print(f"\n  [intégrale BT indisponible: {e}]")


if __name__ == "__main__" and "--exit-side" not in sys.argv:
    main()


# ── (c) Colonne SORTIE (2026-07-02, demande revue) ───────────────────

STOP_LIKE = {"catastrophe_stop", "manual_stop_set", "opp_floor", "prop_trail",
             "s10_trailing", "s8_inlife", "s9_early_exit", "traj_cut",
             "btc_drop_cut", "s8_dead_in_water", "dead_timeout"}
TIMEOUT_LIKE = {"timeout", "runner_ext_timeout"}


def _mark_at(db, sym, ts):
    """Dernier mark ≤ ts (tolérance 180s) dans market.db ticks."""
    r = db.execute("SELECT mark_px FROM ticks WHERE symbol=? AND ts<=? AND ts>=? "
                   "ORDER BY ts DESC LIMIT 1", (sym, int(ts), int(ts) - 180)).fetchone()
    return r[0] if r else None


def exit_side():
    """(c1) slippage d'exécution : fill de sortie vs mark au moment de la
    sortie (ticks 60s), + = payé. (c2) overshoot de niveau pour les règles à
    seuil reconstructible : gross_bps vs niveau théorique (catastrophe,
    s10_trail via MFE−150, prop_trail via arm+(MFE−arm)×ratio)."""
    import json as _json
    mdb = sqlite3.connect(MARKET_DB)
    exec_slip = defaultdict(list)   # groupe → [bps]
    exec_by_strat = defaultdict(list)
    level_over = defaultdict(list)  # reason → [gross − level]
    n_no_tick = 0
    for bot in ("live", "junior", "baby"):
        db = sqlite3.connect(f"/home/crypto/alfred/data/bots/{bot}/bot.db")
        db.row_factory = sqlite3.Row
        for t in db.execute("SELECT symbol, direction, strategy, exit_time, "
                            "exit_price, gross_bps, mfe_bps, signal_info, reason "
                            "FROM trades WHERE exit_time IS NOT NULL"):
            d = parse_dir(t["direction"])
            reason = t["reason"] or ""
            grp = ("stop" if reason in STOP_LIKE
                   else "timeout" if reason in TIMEOUT_LIKE else "autre")
            dt = datetime.fromisoformat(t["exit_time"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            mark = _mark_at(mdb, t["symbol"], dt.timestamp())
            if mark and t["exit_price"]:
                cost = d * (mark / t["exit_price"] - 1) * 1e4
                exec_slip[grp].append(cost)
                exec_by_strat[(t["strategy"], grp)].append(cost)
            else:
                n_no_tick += 1
            # (c2) niveau théorique
            level = None
            if reason == "catastrophe_stop":
                si = {}
                try:
                    si = _json.loads(t["signal_info"] or "{}")
                except Exception:
                    pass
                level = si.get("stop_bps") or (-750.0 if t["strategy"] == "S8" else -1250.0)
            elif reason == "s10_trailing" and t["mfe_bps"]:
                level = t["mfe_bps"] - 150.0
            elif reason == "prop_trail" and t["mfe_bps"]:
                level = 100.0 + (t["mfe_bps"] - 100.0) * 0.65   # S9 bull (arm 100, ratio 0.65)
            if level is not None and t["gross_bps"] is not None:
                level_over[reason].append(t["gross_bps"] - level)
    print("=" * 100)
    print("(c1) SLIPPAGE D'EXÉCUTION EN SORTIE (fill vs mark au moment de la sortie, + = payé)")
    print("     ticks 60s → mesure le spread/impact, pas la latence de règle")
    print("=" * 100)
    for grp in ("stop", "timeout", "autre"):
        print(f"    {grp:<8} {stats(exec_slip.get(grp, []))}")
    print("\n  Par stratégie × groupe :")
    for (s, g), vals in sorted(exec_by_strat.items()):
        print(f"    {s:<4} {g:<8} {stats(vals)}")
    if n_no_tick:
        print(f"  [{n_no_tick} sorties sans tick de référence (hors fenêtre market.db) — exclues]")
    print()
    print("=" * 100)
    print("(c2) OVERSHOOT DE NIVEAU (gross_bps réel − niveau de déclenchement, − = fill au-delà)")
    print("     = latence 20s + gap : ce que le booking synthétique du BT ne modélise pas")
    print("=" * 100)
    for reason, vals in sorted(level_over.items()):
        print(f"    {reason:<18} {stats(vals)}")


if __name__ == "__main__" and "--exit-side" in sys.argv:
    exit_side()
