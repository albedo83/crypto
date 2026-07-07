"""Cap k×equity : robustesse + hybride (2026-07-07).

Suite du premise-gate ([[project_proportional_cap_2026_07]]) : k=0.3 = 2/4
strict mais concentration 30% constante, 0 cascade, 9× PnL 28m. Le $86k venait
d'UN chemin (28m ending today) — zone d'artefacts (fort compounding). Ici on
attaque la robustesse et l'hybride, avec margin_check partout (garde-fou validé).

Volet 1 — ROBUSTESSE dates : le bénéfice k=0.3 survit-il à des fenêtres
  glissantes (start décalés) ? Un vrai edge tient sur plusieurs époques ; un
  artefact single-path meurt.
Volet 2 — PLATEAU vs PIC : k∈{0.2,0.25,0.3,0.35,0.4} — 0.3 est-il sur un
  plateau (robuste) ou un pic isolé (fragile) ?
Volet 3 — HYBRIDE : max(F, k×equity), F∈{300,500} — préserve le petit capital,
  débloque le gros. Récupère-t-il le strict 4/4 ? garde-t-il 0 cascade ?

Rien n'est auto-shippé — rapport, l'utilisateur tranche.
Usage : python3 -m backtests.backtest_cap_robustness
"""
import sys

sys.path.insert(0, "/home/crypto")

from datetime import datetime
from dateutil.relativedelta import relativedelta

import backtests.backtest_rolling as br
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features
from alfred.settings import DEFAULT_PARAMS


def main():
    print("Loading…", flush=True)
    data = load_3y_candles(); features = build_features(data)
    sectors = compute_sector_features(features, data)
    oi, funding, dxy = load_oi(), load_funding(), load_dxy()
    end_ms = max(c["t"] for c in data["BTC"])
    end_dt = datetime.fromtimestamp(end_ms / 1000).astimezone()
    br._P = DEFAULT_PARAMS

    def run(start_ms, e_ms, cap_fn=None):
        r = run_window(features, data, sectors, dxy, start_ms, e_ms,
                       start_capital=500.0, oi_data=oi, funding_data=funding,
                       apply_adaptive_modulator=True, aligned=True,
                       margin_check=True, mfe_on_close=True, max_notional_fn=cap_fn)
        return r["end_capital"], r.get("max_dd_pct", 0.0), r.get("n_margin_skip", 0)

    prop = lambda k: (lambda kk: (lambda coin, ts, cap: kk * cap))(k)
    hybrid = lambda F, k: (lambda FF, kk: (lambda coin, ts, cap: max(FF, kk * cap)))(F, k)

    # ── Volet 1 : robustesse dates (fenêtres glissantes de 24m, start décalé) ──
    print("\n═══ VOLET 1 — ROBUSTESSE : k=0.3 vs fixe $500 sur fenêtres glissantes 24m ═══")
    print(f"  {'fenêtre (start)':<20} {'fixe$':>9} {'k=0.3$':>10} {'Δ%':>8} "
          f"{'DDfix':>7} {'DDk3':>7} {'cascK3':>7}")
    wins = 0; n_win = 0
    for back in (28, 25, 22, 19, 16, 13, 10):
        s = int((end_dt - relativedelta(months=back)).timestamp() * 1000)
        e = int((end_dt - relativedelta(months=back - 24)).timestamp() * 1000) if back > 24 else end_ms
        ef, df, cf = run(s, e)
        ek, dk, ck = run(s, e, prop(0.3))
        lbl = f"−{back}m→−{max(back-24,0)}m"
        better = ek > ef and dk >= df - 3   # PnL+ ET DD pas bien pire
        wins += better; n_win += 1
        sd = datetime.fromtimestamp(s/1000).strftime('%Y-%m')
        print(f"  {sd} ({lbl:<9}) {ef:>9.0f} {ek:>10.0f} {(ek/ef-1)*100:>+7.0f}% "
              f"{df:>6.0f}% {dk:>6.0f}% {ck:>7} {'✓' if better else '·'}")
    print(f"  → k=0.3 bat le fixe (PnL+ & DD≈) sur {wins}/{n_win} fenêtres glissantes")

    # ── Volet 2 : plateau vs pic (4 fenêtres canoniques) ──
    print("\n═══ VOLET 2 — PLATEAU vs PIC autour de k=0.3 (fin = aujourd'hui) ═══")
    WINS = (("28m", 28), ("12m", 12), ("6m", 6), ("3m", 3))
    starts = {w: int((end_dt - relativedelta(months=m)).timestamp() * 1000) for w, m in WINS}
    base = {w: run(starts[w], end_ms) for w, _ in WINS}
    print(f"  {'k':<6} " + " ".join(f'{w:>12}' for w, _ in WINS) + "   (Δ$ vs fixe)")
    for k in (0.2, 0.25, 0.3, 0.35, 0.4):
        cells = []
        for w, _ in WINS:
            e, d, c = run(starts[w], end_ms, prop(k))
            cells.append(f"{e-base[w][0]:>+8.0f}/{c:<3}")
        print(f"  k={k:<4} " + " ".join(f'{c:>12}' for c in cells))
    print("  (format Δ$/cascades ; plateau = Δ stable autour de 0.3, pic = 0.3 isolé)")

    # ── Volet 3 : hybride max(F, k×eq) ──
    print("\n═══ VOLET 3 — HYBRIDE max(F, 0.3×equity) — 4 fenêtres, vs fixe $500 ═══")
    print(f"  {'variante':<16} {'fen':<5} {'end$':>8} {'Δ$':>9} {'DD':>6} {'ΔDD':>6} {'casc':>6} {'ok'}")
    for F in (300, 500):
        passes = 0
        for w, _ in WINS:
            e, d, c = run(starts[w], end_ms, hybrid(F, 0.3))
            eb, db, cb = base[w]
            ok = (e >= eb - 0.01) and (d >= db - 0.01) and (c <= cb * 1.2 + 1)
            passes += ok
            print(f"  max(${F},0.3eq){'':<3} {w:<5} {e:>8.0f} {e-eb:>+9.0f} {d:>5.0f}% "
                  f"{d-db:>+5.0f} {c:>6} {'✓' if ok else '·'}")
        print(f"    → max(${F},0.3eq) : {passes}/4" + ("  *** PASS ***" if passes == 4 else ""))


if __name__ == "__main__":
    main()
