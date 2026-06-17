"""Slow-bleed cut sur S5 LONG — walk-forward, INCRÉMENTAL au-dessus de traj_cut.

Motivation (live senior, 2026-06) : S5 LONG = 70 % WR mais payoff 0.63 — le book
net +$25 ne tient que sur 2 outliers (CRV +$48, UNI +$18). Le pire perdant, WLD à
MAE −974 bps, a ÉCHAPPÉ à traj_cut (chute BRUTALE : déclin raide + collé au MAE) ET
à dead_timeout (MFE 160 > cap 150). Hémorragie LENTE sur 42 h, coupée à la main.

Hypothèse : une règle « hold_h ≥ X ET cur ≤ −Y bps » sur S5 LONG rattrape ces bleeds
lents SANS toucher les runners (CRV a fait +1990 depuis un MAE de seulement −361).

⚠️ Méthodo : le mode ALIGNED ignore les hooks R&D (sentinelle __aligned_hold__).
On valide donc en mode LEGACY + hook `inlife_exit_extra`, EXACTEMENT comme traj_cut
l'a été (backtest_trajectory_cut_v2.py). Pour mesurer la valeur INCRÉMENTALE du
slow-bleed au-dessus de traj_cut, on reconstruit traj_cut comme hook (params shippés
v12.7.1, bear) et on compare :
   baseline  = legacy + dead_timeout + traj_cut(seul)
   variante  = legacy + dead_timeout + traj_cut + slow_bleed
Le Δ isole le slow-bleed. (Caveat : legacy ≠ 100 % config live — pas de prop_trail/s8
rules ni sizing aligned — mais c'est la même base que celle qui a validé traj_cut, et
on mesure un Δ relatif, pas un niveau absolu.)

Critère strict 4/4 : ΔPnL ≥ 0 ET ΔDD ≤ +2pp vs baseline(traj_cut) sur LES 4 fenêtres.
DD prioritaire. Reasons : traj_cut / slow_bleed_cut.
"""
from __future__ import annotations
import json
import time
from collections import Counter
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta  # type: ignore

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import run_window, load_dxy, load_oi, load_funding
from analysis.bot.config import (
    DEAD_TIMEOUT_LEAD_HOURS, DEAD_TIMEOUT_MFE_CAP_BPS,
    DEAD_TIMEOUT_MAE_FLOOR_BPS, DEAD_TIMEOUT_SLACK_BPS,
)

WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]
EARLY_EXIT = dict(
    exit_lead_candles=int(DEAD_TIMEOUT_LEAD_HOURS // 4),
    mfe_cap_bps=DEAD_TIMEOUT_MFE_CAP_BPS,
    mae_floor_bps=DEAD_TIMEOUT_MAE_FLOOR_BPS,
    slack_bps=DEAD_TIMEOUT_SLACK_BPS,
)
# traj_cut shippé (v12.7.1) — bear-conditionné
TRAJ = dict(decline=100.0, t_since_mfe=4.0, slack=100.0, min_loss=-200.0)


def load_all():
    print("Loading data...")
    t0 = time.time()
    data = load_3y_candles()
    features = build_features(data)
    sec = compute_sector_features(features, data)
    dxy, oi, fund = load_dxy(), load_oi(), load_funding()
    end_ts = max(c["t"] for c in data["BTC"])
    print(f"  loaded in {time.time()-t0:.1f}s")
    return dict(data=data, features=features, sec=sec, dxy=dxy, oi=oi,
                funding=fund, end_ts=end_ts)


def window_specs(end_ts_ms):
    end_dt = datetime.fromtimestamp(end_ts_ms / 1000, tz=timezone.utc)
    return [(label, int((end_dt - relativedelta(months=m)).timestamp() * 1000), end_ts_ms)
            for label, m in WINDOWS]


def _traj_fires(snap):
    """Reproduit traj_cut v12.7.1 (S5, bear). True = couper."""
    if snap["strat"] != "S5":
        return False
    cur, mfe = snap.get("cur_bps", 0.0), snap.get("mfe_bps", 0.0)
    mae, tsm = snap.get("mae_bps", 0.0), snap.get("time_since_mfe_h", 0.0)
    if cur != cur or mfe != mfe or mae != mae or tsm != tsm:
        return False
    if tsm < TRAJ["t_since_mfe"]:
        return False
    if cur > TRAJ["min_loss"]:
        return False
    if (cur - mae) > TRAJ["slack"]:
        return False
    if (mfe - cur) / max(tsm, 1.0) < TRAJ["decline"]:
        return False
    return snap.get("btc_z", 0.0) < -0.5          # R1 bear


def make_hook(*, slow=None):
    """slow=None → traj_cut seul (baseline). slow=dict → + slow_bleed S5 LONG."""
    st = {"traj": 0, "bleed": 0}

    def hook(snap):
        if _traj_fires(snap):
            st["traj"] += 1
            return (True, "traj_cut")
        if slow is not None and snap["strat"] == "S5" and snap["dir"] == 1:
            bz, cur, hh = snap.get("btc_z", 0.0), snap.get("cur_bps", 0.0), snap.get("hold_h", 0.0)
            if cur == cur and bz == bz:
                if (not slow["bear_only"] or bz < -0.5) and hh >= slow["hold"] and cur <= slow["loss"]:
                    st["bleed"] += 1
                    return (True, "slow_bleed_cut")
        return None

    return hook, st


def run_one(ctx, s, e, hook):
    return run_window(
        ctx["features"], ctx["data"], ctx["sec"], ctx["dxy"], s, e,
        start_capital=1000.0,
        oi_data=ctx["oi"], funding_data=ctx["funding"],
        early_exit_params=EARLY_EXIT,
        apply_adaptive_modulator=True,
        inlife_exit_extra=hook,            # legacy mode (aligned=False) → hook actif
    )


def run_set(ctx, hook, tag=""):
    out = {}
    for label, s, e in window_specs(ctx["end_ts"]):
        t0 = time.time()
        r = run_one(ctx, s, e, hook)
        nb = sum(1 for t in r["trades"] if t["reason"] == "slow_bleed_cut")
        nt = sum(1 for t in r["trades"] if t["reason"] == "traj_cut")
        out[label] = dict(pnl_pct=r["pnl_pct"], dd_pct=r["max_dd_pct"],
                          n_trades=r["n_trades"], n_bleed=nb, n_traj=nt)
        print(f"  {tag:16} {label}: pnl={r['pnl_pct']:+9.1f}%  DD={r['max_dd_pct']:6.1f}%  "
              f"trades={r['n_trades']:4d}  traj={nt:3d} bleed={nb:3d}  ({time.time()-t0:.0f}s)")
    return out


def verdict(base, var):
    pp = dd = 0
    deltas = {}
    for label, _ in WINDOWS:
        d_pnl = var[label]["pnl_pct"] - base[label]["pnl_pct"]
        d_dd = var[label]["dd_pct"] - base[label]["dd_pct"]   # dd signé négatif
        deltas[label] = (d_pnl, d_dd)
        if d_pnl >= 0:
            pp += 1
        if d_dd >= -2.0:
            dd += 1
    return pp, dd, (pp == 4 and dd == 4), deltas


def main():
    ctx = load_all()

    print("\n[1] Baseline = legacy + dead_timeout + traj_cut(seul)")
    bhook, bst = make_hook(slow=None)
    base = run_set(ctx, bhook, tag="base(traj)")
    print(f"  traj_cut fired (cumul sur les 4 fenêtres, recouvrantes) = {bst['traj']}")

    print("\n[2] Variantes : traj_cut + slow_bleed (S5 LONG)")
    HOLDS = [16, 24, 36]
    LOSSES = [-400.0, -500.0, -600.0]
    REGIMES = [("bear", True), ("uncond", False)]
    results = {}
    for rname, bear in REGIMES:
        for hh in HOLDS:
            for ly in LOSSES:
                name = f"{rname}_h{hh}_l{int(-ly)}"
                hook, st = make_hook(slow=dict(hold=hh, loss=ly, bear_only=bear))
                res = run_set(ctx, hook, tag=name)
                pp, dd, strict, deltas = verdict(base, res)
                results[name] = dict(res=res, pp=pp, dd=dd, strict=strict,
                                     deltas=deltas, bleed=st["bleed"])
                print(f"     → {name:18} PnL {pp}/4  DD {dd}/4  bleed_fired={st['bleed']:3}  "
                      f"{'STRICT 4/4 ✓✓' if strict else ''}")

    print("\n[3] Récap (trié par pass PnL, puis sumΔPnL)")
    def key(it):
        s = it[1]
        return (s["pp"], sum(d[0] for d in s["deltas"].values()), s["dd"])
    print(f"\n{'variant':<20} {'PnL/4':>6} {'DD/4':>5} {'sumΔPnL':>10}  "
          f"{'28mΔ':>9} {'12mΔ':>8} {'6mΔ':>8} {'3mΔ':>8}  {'bleed(28/12/6/3)':>16}")
    for name, s in sorted(results.items(), key=key, reverse=True):
        d = s["deltas"]
        sd = sum(x[0] for x in d.values())
        dd_str = " ".join(f"{d[l][0]:+8.1f}" for l, _ in WINDOWS)
        bl = "/".join(str(s["res"][l]["n_bleed"]) for l, _ in WINDOWS)
        flag = "  STRICT4/4" if s["strict"] else ""
        print(f"{name:<20} {s['pp']:>4}/4 {s['dd']:>3}/4 {sd:>+10.1f}  {dd_str}  {bl:>16}{flag}")

    with open("/home/crypto/backtests/s5_slow_bleed_artifacts.json", "w") as f:
        json.dump(dict(baseline={l: base[l] for l, _ in WINDOWS},
                       results={n: {"res": s["res"], "pp": s["pp"], "dd": s["dd"],
                                    "strict": s["strict"], "bleed": s["bleed"]}
                                for n, s in results.items()}), f, indent=2, default=str)
    print("\nArtifacts → backtests/s5_slow_bleed_artifacts.json")


if __name__ == "__main__":
    main()
