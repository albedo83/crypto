"""EDA prémisse — la durée de hold (48h) pénalise-t-elle, surtout en BTC choppy ?

Intuition utilisateur : « en 48h le BTC fait du yo-yo, le hold est trop long ».
Avant tout sweep walk-forward (cap de hold = coûteux + slot-substitution), on
valide la prémisse sur UN run canonique, en lecture seule :

  A. Outcome par durée de hold réelle (net/WR par bucket d'heures).
  B. Timing du MFE : le pic arrive-t-il tôt puis on rend (giveback) ?
  C. CHOP BTC pendant le hold : path_length / |déplacement net| du BTC sur
     [entrée, sortie]. Hypothèse = chop élevé → trade net-négatif.
  D. Contrefactuel RÉEL (pas un sweep) : pour H ∈ {12..40h}, sortie forcée au
     plus tôt de (sortie réelle, H) en relisant le chemin ur_bps déjà dumpé.
     Somme gross bps vs baseline. Si même le naïf n'améliore rien → prémisse
     morte (on ne lance pas le sweep). NB : ignore la slot-substitution
     (volontaire — c'est une borne optimiste ; si négatif ici, c'est mort).

Le contrefactuel compare gross-vs-gross (ur_bps), les frais ~constants
s'annulent dans le delta. Focus aussi sur le régime bear (btc_z < -0.5), le
contexte actuel.

Usage : python3 -m backtests.eda_hold_duration
"""
import statistics as st

from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features

HOLD_CAPS_H = [12, 16, 20, 24, 32, 40]
INTERVAL_H = 4


def btc_chop(btc_closes, btc_idx, entry_t, exit_t):
    """path_length / |net displacement| du BTC sur le hold. >1 = yo-yo.
    Renvoie aussi la vol réalisée (somme |ret 4h|) en bps."""
    i0 = btc_idx.get(entry_t)
    i1 = btc_idx.get(exit_t)
    if i0 is None or i1 is None or i1 <= i0:
        return None, None
    seg = btc_closes[i0:i1 + 1]
    if len(seg) < 2 or seg[0] <= 0:
        return None, None
    path = sum(abs(seg[k] / seg[k - 1] - 1) for k in range(1, len(seg))) * 1e4
    net_disp = abs(seg[-1] / seg[0] - 1) * 1e4
    chop = path / net_disp if net_disp > 1e-6 else float("inf")
    return chop, path


def main():
    print("Chargement données 3 ans…")
    data = load_3y_candles()
    features = build_features(data)
    sector_features = compute_sector_features(features, data)
    dxy = load_dxy(); oi = load_oi(); funding = load_funding()
    latest = max(c["t"] for c in data["BTC"])
    start = latest - int(28 * 30.4 * 86400 * 1000)

    btc = data["BTC"]
    btc_closes = [c["c"] for c in btc]
    btc_idx = {c["t"]: k for k, c in enumerate(btc)}

    print("Run backtest 28m (aligné, margin, mfe_on_close, trajectory dump)…")
    dump = "/tmp/claude-0/-home-crypto/d2ed5aca-4413-4a39-a3db-3ff8b25d706c/scratchpad/traj.json"
    r = run_window(features, data, sector_features, dxy, start, latest,
                   start_capital=1000.0, oi_data=oi, funding_data=funding,
                   apply_adaptive_modulator=True, aligned=True,
                   margin_check=True, mfe_on_close=True,
                   trajectory_dump_path=dump)
    trades = [t for t in r["trades"] if t.get("trajectory")]
    print(f"\n{len(trades)} trades avec trajectoire | net global {sum(t['net'] for t in r['trades']):+.0f} bps")

    # enrichit chaque trade
    rows = []
    for t in trades:
        traj = t["trajectory"]
        hold_h = len(traj) * INTERVAL_H
        mfe_at_h = t.get("mfe_held", 0) * INTERVAL_H
        chop, path = btc_chop(btc_closes, btc_idx, t["entry_t"], t["exit_t"])
        bz0 = traj[0]["btc_z"] if traj else 0.0
        rows.append({**t, "hold_h": hold_h, "mfe_at_h": mfe_at_h,
                     "chop": chop, "btc_path": path, "bz0": bz0, "traj": traj})

    def tbl(name, groups):
        print(f"\n=== {name} ===")
        for lbl, g in groups:
            if not g:
                print(f"  {lbl:<22} n=0"); continue
            net = [x["net"] for x in g]
            wr = 100 * sum(1 for v in net if v > 0) / len(g)
            print(f"  {lbl:<22} n={len(g):<4} WR={wr:3.0f}%  "
                  f"net_moy={st.mean(net):+7.0f}  net_sum={sum(net):+8.0f}")

    # A. par durée de hold
    def hb(lo, hi):
        return [x for x in rows if lo <= x["hold_h"] < hi]
    tbl("A. Outcome par durée de hold (réelle)", [
        ("<12h", hb(0, 12)), ("12-24h", hb(12, 24)), ("24-36h", hb(24, 36)),
        ("36-48h", hb(36, 48)), (">=48h", hb(48, 1e9))])

    # B. timing MFE / giveback
    gb = [x["mfe_bps"] - x["net"] for x in rows]
    early = [x for x in rows if x["mfe_at_h"] <= 12]
    print(f"\n=== B. Timing MFE & giveback ===")
    print(f"  MFE atteint <=12h : {100*len(early)/len(rows):.0f}% des trades")
    print(f"  giveback (mfe-net) moy={st.mean(gb):+.0f} bps  médian={st.median(gb):+.0f}")
    winners = [x for x in rows if x["net"] > 0]
    if winners:
        wr_mfe = [x["mfe_at_h"] for x in winners]
        print(f"  gagnants : MFE atteint à {st.mean(wr_mfe):.0f}h en moy "
              f"(médian {st.median(wr_mfe):.0f}h) sur hold moy {st.mean([x['hold_h'] for x in winners]):.0f}h")

    # C. CHOP BTC pendant le hold
    cr = [x for x in rows if x["chop"] is not None and x["chop"] != float("inf")]
    if cr:
        med = st.median(x["chop"] for x in cr)
        tbl(f"C. Outcome par chop BTC (médiane={med:.2f}, >1=yoyo)", [
            (f"chop<{med:.2f} (directionnel)", [x for x in cr if x["chop"] < med]),
            (f"chop>={med:.2f} (yo-yo)", [x for x in cr if x["chop"] >= med])])
        try:
            import numpy as np
            c = np.array([x["chop"] for x in cr]); n = np.array([x["net"] for x in cr])
            print(f"  corr(chop, net) = {np.corrcoef(c, n)[0,1]:+.3f}  (n={len(cr)})")
        except Exception:
            pass

    # D. contrefactuel cap de hold (gross, par trade)
    print(f"\n=== D. Contrefactuel cap de hold (gross bps, somme) ===")
    base = sum(x["traj"][-1]["ur_bps"] for x in rows)
    print(f"  baseline (sortie réelle)            net_gross_sum={base:+9.0f}")
    for H in HOLD_CAPS_H:
        cap_cand = H // INTERVAL_H  # index de bougie max
        s = 0.0
        for x in rows:
            traj = x["traj"]
            # held va de 0..N ; on sort à l'index min(cap, dernier dispo)
            idx = min(cap_cand, len(traj) - 1)
            s += traj[idx]["ur_bps"]
        delta = s - base
        print(f"  cap {H:>2}h  net_gross_sum={s:+9.0f}   Δ vs baseline={delta:+8.0f}")

    # D-bis : focus régime bear (btc_z < -0.5) — contexte actuel
    bear = [x for x in rows if x["bz0"] < -0.5]
    if bear:
        print(f"\n=== D-bis. Idem mais BEAR uniquement (btc_z<-0.5, n={len(bear)}) ===")
        base_b = sum(x["traj"][-1]["ur_bps"] for x in bear)
        print(f"  baseline bear                       net_gross_sum={base_b:+9.0f}")
        for H in HOLD_CAPS_H:
            cap_cand = H // INTERVAL_H
            s = sum(x["traj"][min(cap_cand, len(x["traj"]) - 1)]["ur_bps"] for x in bear)
            print(f"  cap {H:>2}h  net_gross_sum={s:+9.0f}   Δ vs baseline={s-base_b:+8.0f}")

    print("\nPrémisse OK si : (B) MFE arrive tôt + giveback gros, ou (C) chop>=med "
          "nettement plus mauvais, ou (D) un cap améliore franchement (surtout bear).")


if __name__ == "__main__":
    main()
