"""Extension prop_trail à S5 — confirmation ALIGNED (gate de ship).

Constat live senior : S5 LONG WR 70 % mais payoff 0.63 — les gagnants RENDENT leur
MFE (4 scratches montés à +265/+334 bps, finis à +15/+61 → ~250 bps rendus chacun).
Or `prop_trail` (verrou proportionnel du MFE) ne couvre QUE S9/bull ; S5 n'a AUCUN
trailing (giveback S5 = alerte-seulement). Levier inexploré.

Hypothèse : un verrou proportionnel sur S5 capture une partie du MFE rendu → relève le
gain moyen → améliore le payoff, SANS toucher les perdants. Le verrou est PROPORTIONNEL
(stop = arm + (mfe−arm)×lock) donc il laisse courir les gros (CRV +1990, UNI +893).

Mécanique : `prop_trail_rule` existe déjà et est générique (lit prop_trail_params[strat]).
On injecte une config S5 via `prop_trail_override` (merge), aligned. Baseline = défaut
(S9 seul) = config live → doit reproduire 819.9/440.6/170.2/6.1 (zéro régression).

Grille : arm ∈ {150,200,300} bps × lock ∈ {0.4,0.5,0.65}, MÊME valeur tous régimes.
Diag : PnL/nb S5, nb sorties prop_trail — vérifier qu'on capture sans tuer les runners.
Critère strict 4/4 : ΔPnL ≥ 0 ET ΔDD ≥ −2pp vs baseline sur LES 4 fenêtres.
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta  # type: ignore

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from backtests.backtest_rolling import run_window, load_dxy, load_oi, load_funding

WINDOWS = [("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3)]


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


def s5_cfg(arm, lock):
    """Même verrou sur les 3 régimes."""
    return {"S5": {"bear": (arm, lock), "neutral": (arm, lock), "bull": (arm, lock)}}


def run_set(ctx, override, tag=""):
    out = {}
    for label, s, e in window_specs(ctx["end_ts"]):
        t0 = time.time()
        r = run_window(ctx["features"], ctx["data"], ctx["sec"], ctx["dxy"], s, e,
                       start_capital=1000.0, oi_data=ctx["oi"], funding_data=ctx["funding"],
                       apply_adaptive_modulator=True, aligned=True, margin_check=True,
                       prop_trail_override=override)
        npt = sum(1 for t in r["trades"] if t["reason"] == "prop_trail" and t["strat"] == "S5")
        s5 = r["by_strat"].get("S5", {})
        out[label] = dict(pnl_pct=r["pnl_pct"], dd_pct=r["max_dd_pct"], n_trades=r["n_trades"],
                          s5_pnl=s5.get("pnl", 0.0), s5_n=s5.get("n", 0), s5_wr=s5.get("wr", 0),
                          n_pt_s5=npt)
        print(f"  {tag:14} {label}: pnl={r['pnl_pct']:+9.1f}%  DD={r['max_dd_pct']:6.1f}%  "
              f"S5pnl={s5.get('pnl',0):>8.0f} S5n={s5.get('n',0):>3} ptS5={npt:>3}  ({time.time()-t0:.0f}s)")
    return out


def verdict(base, var):
    pp = dd = 0
    deltas = {}
    for label, _ in WINDOWS:
        d_pnl = var[label]["pnl_pct"] - base[label]["pnl_pct"]
        d_dd = var[label]["dd_pct"] - base[label]["dd_pct"]
        deltas[label] = (d_pnl, d_dd)
        pp += d_pnl >= 0
        dd += d_dd >= -2.0
    return pp, dd, (pp == 4 and dd == 4), deltas


def main():
    ctx = load_all()

    print("\n[1] Baseline ALIGNED (prop_trail défaut = S9 seul) — doit == config live")
    base = run_set(ctx, None, tag="baseline")

    print("\n[2] Grille prop_trail S5 (arm × lock, tous régimes)")
    configs = [(f"a{arm}_l{int(lock*100)}", s5_cfg(arm, lock))
               for arm in (150, 200, 300) for lock in (0.40, 0.50, 0.65)]
    results = {}
    for name, ov in configs:
        res = run_set(ctx, ov, tag=name)
        pp, dd, strict, deltas = verdict(base, res)
        results[name] = dict(res=res, pp=pp, dd=dd, strict=strict, deltas=deltas)
        print(f"     → {name:14} PnL {pp}/4  DD {dd}/4  {'STRICT 4/4 ✓✓' if strict else ''}")

    print("\n[3] Récap (trié par pass PnL, puis sumΔPnL)")
    print(f"\n{'config':<12} {'PnL/4':>6} {'DD/4':>5} {'sumΔPnL':>9}  "
          f"{'28mΔ':>8} {'12mΔ':>8} {'6mΔ':>7} {'3mΔ':>7}  {'ptS5(28)':>8}  verdict")
    def key(it):
        s = it[1]
        return (s["pp"], sum(d[0] for d in s["deltas"].values()), s["dd"])
    for name, s in sorted(results.items(), key=key, reverse=True):
        d = s["deltas"]
        sd = sum(x[0] for x in d.values())
        ds = " ".join(f"{d[l][0]:+8.1f}" for l, _ in WINDOWS)
        npt = s["res"]["28m"]["n_pt_s5"]
        v = "STRICT4/4" if s["strict"] else f"{s['pp']}/4"
        print(f"{name:<12} {s['pp']:>4}/4 {s['dd']:>3}/4 {sd:>+9.1f}  {ds}  {npt:>8}  {v}")

    # Diag S5-spécifique sur 28m : la règle améliore-t-elle le BOOK S5 ?
    print("\n[4] Effet sur le BOOK S5 (28m) — baseline vs meilleures configs")
    b = base["28m"]
    print(f"  baseline      S5: pnl={b['s5_pnl']:>8.0f}  n={b['s5_n']}  wr={b['s5_wr']}")
    for name, s in sorted(results.items(), key=key, reverse=True)[:4]:
        r = s["res"]["28m"]
        print(f"  {name:<12}  S5: pnl={r['s5_pnl']:>8.0f}  n={r['s5_n']}  wr={r['s5_wr']}  ptS5={r['n_pt_s5']}")

    with open("/home/crypto/backtests/s5_prop_trail_artifacts.json", "w") as f:
        json.dump(dict(baseline={l: base[l] for l, _ in WINDOWS},
                       results={n: {"res": s["res"], "pp": s["pp"], "dd": s["dd"],
                                    "strict": s["strict"]} for n, s in results.items()}),
                  f, indent=2, default=str)
    print("\nArtifacts → backtests/s5_prop_trail_artifacts.json")


if __name__ == "__main__":
    main()
