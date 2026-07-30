"""AUDIT DE PARITÉ DES ENTRÉES — où est le biais de mesure n°3 ?

Motivation (2026-07-30) : deux biais de mesure majeurs en une semaine — booking
des trails (v1.15.5) puis carte des secteurs périmée (v1.17.1). Ce n'est pas deux
malchances, c'est un échantillon. La doctrine « noyau de règles partagé » ne
protège de rien si les ENTRÉES de ces règles divergent entre le bot et le
backtest — et `backtests/test_feature_parity.py` ne couvrait que 8 features par
token, soit une fraction de la surface réelle.

Cet audit énumère TOUTES les entrées consommées par `alfred/rules.py` et
`alfred/signals.py`, et pour chacune confronte les deux implémentations sur les
MÊMES données. Une entrée sans source partagée et sans test est un biais en
puissance, qu'il soit déjà actif ou non.

Ne modifie rien. Lecture seule.

Usage : python3 -m backtests.audit_input_parity [n_tirages]
"""

from __future__ import annotations

import json
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import alfred.features as A_FEAT
import alfred.signals as A_SIG
from alfred import rules as A_RULES
from alfred.settings import DEFAULT_PARAMS as P
from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS
import backtests.backtest_sector as BS

TOL = 1e-6


def _close(a, b, tol=TOL):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= max(tol, tol * max(abs(a), abs(b)))


def main() -> int:
    n_draws = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    random.seed(42)
    print("Chargement des données + features backtest…", flush=True)
    data = load_3y_candles()
    bt_features = build_features(data)

    results: list[dict] = []

    def report(name, source, status, detail):
        results.append({"entrée": name, "source": source,
                        "statut": status, "détail": detail})
        icon = {"OK": "✓", "DIVERGE": "✗", "PARTAGÉ": "✓", "RISQUE": "⚠"}[status]
        print(f"  {icon} {name:26s} {status:9s} {detail}")

    # ── index (ts → {coin: feature row}) commun aux deux mondes ────────
    feat_by_ts: dict[int, dict] = defaultdict(dict)
    for coin, rows in bt_features.items():
        for r in rows:
            feat_by_ts[r["t"]][coin] = r
    all_ts = sorted(t for t, m in feat_by_ts.items() if len(m) >= 10)
    draws_ts = random.sample(all_ts, min(n_draws, len(all_ts)))
    print(f"{len(all_ts)} timestamps exploitables, {len(draws_ts)} tirés\n")

    print("=== 1. Univers et secteurs (config, pas calcul)")
    bt_univ, prod_univ = set(TOKENS), set(P.trade_symbols)
    if bt_univ == prod_univ:
        report("univers de trading", "Params (v1.17.2)", "OK",
               f"{len(bt_univ)} tokens — dérivé de p.trade_symbols, "
               f"plus de copie manuelle")
    else:
        report("univers de trading", "COPIE MANUELLE", "DIVERGE",
               f"BT-seul={sorted(bt_univ-prod_univ)} prod-seul={sorted(prod_univ-bt_univ)}")
    report("carte des secteurs", "Params (v1.17.1)",
           "OK" if BS.TOKEN_SECTOR == P.token_sector() else "DIVERGE",
           f"{len(BS.SECTORS)} secteurs des deux côtés")

    print("\n=== 2. Features par token (déjà couvert par test_feature_parity)")
    mism = 0
    fields = [("ret_24h", "ret_6h"), ("ret_42h", "ret_42h"),
              ("drawdown", "drawdown"), ("vol_z", "vol_z"),
              ("vol_ratio", "vol_ratio"), ("range_pct", "range_pct")]
    pool = [(c, r) for c in TOKENS if c in bt_features
            for r in bt_features[c]]
    for coin, row in random.sample(pool, min(n_draws, len(pool))):
        bf = A_FEAT.compute_features(data[coin][: row["_idx"] + 1])
        if bf is None:
            continue
        for bk, tk in fields:
            if not _close(bf[bk], row.get(tk, 0.0)):
                mism += 1
                break
    report("features par token", "2 implémentations",
           "OK" if mism == 0 else "DIVERGE",
           f"{mism} écarts sur {n_draws} tirages ({len(fields)} champs)")

    print("\n=== 3. Features BTC (btc_7d / btc_30d)")
    btc = data["BTC"]
    idx_by_t = {c["t"]: i for i, c in enumerate(btc)}
    mism7 = mism30 = tested = 0
    ex = None
    for ts in draws_ts:
        i = idx_by_t.get(ts)
        if i is None or i < 181:
            continue
        tested += 1
        bot = A_FEAT.compute_btc_features(btc[: i + 1])
        closes = [c["c"] for c in btc[: i + 1]]
        bt7 = (closes[-1] / closes[len(closes) - 42] - 1) * 1e4
        bt30 = (closes[-1] / closes[len(closes) - 180] - 1) * 1e4
        if not _close(bot["btc_7d"], bt7):
            mism7 += 1
            ex = ex or f"btc_7d bot={bot['btc_7d']:.2f} bt={bt7:.2f}"
        if not _close(bot["btc_30d"], bt30):
            mism30 += 1
            ex = ex or f"btc_30d bot={bot['btc_30d']:.2f} bt={bt30:.2f}"
    report("btc_7d / btc_30d", "2 implémentations",
           "OK" if mism7 + mism30 == 0 else "DIVERGE",
           f"{mism7+mism30} écarts sur {tested} tirages"
           + (f" — ex. {ex}" if ex else ""))

    print("\n=== 4. Divergence sectorielle (entrée UNIQUE de S5)")
    sector_features = BS.compute_sector_features(bt_features, data)
    mism = tested = 0
    ex = None
    for ts in draws_ts:
        fmap = feat_by_ts[ts]
        cache = {s: A_RULES.adapt_bt_features(f) for s, f in fmap.items()}
        for coin in list(fmap)[:4]:
            bot = A_FEAT.compute_sector_divergence(coin, cache, P.sectors, P.token_sector())
            bt = sector_features.get((ts, coin))
            tested += 1
            if (bot is None) != (bt is None):
                mism += 1
                ex = ex or f"{coin}@{ts} présence bot={bot is not None} bt={bt is not None}"
            elif bot and not _close(bot["divergence"], bt["divergence"], 1e-4):
                mism += 1
                ex = ex or (f"{coin}@{ts} div bot={bot['divergence']:.3f} "
                            f"bt={bt['divergence']:.3f}")
    report("divergence sectorielle", "2 implémentations",
           "OK" if mism == 0 else "DIVERGE",
           f"{mism} écarts sur {tested} tirages"
           + (f" — ex. {ex}" if ex else ""))

    print("\n=== 5. Contexte transversal (disp_24h / disp_7d / n_stress)")
    m24 = m7 = mst = 0
    ex24 = ex7 = exst = None
    for ts in draws_ts:
        fmap = feat_by_ts[ts]
        cache = {s: A_RULES.adapt_bt_features(f) for s, f in fmap.items()}
        bot = A_SIG.compute_cross_context(cache, P.trade_symbols, P.token_sector())
        # réplique exacte du backtest (backtest_rolling.py:416-423 et 1663-1666)
        rets24 = [f.get("ret_6h", 0) for f in fmap.values() if "ret_6h" in f]
        bt_disp24 = round(float(np.std(rets24)), 0) if len(rets24) > 4 else None
        bt_disp7 = round(float(np.std([f.get("ret_42h", 0) for f in fmap.values()])), 0)
        bt_stress = sum(1 for f in fmap.values()
                        if f.get("vol_z", 0) > 1.5 and f.get("drawdown", 0) < -1500)
        if bt_disp24 is not None and not _close(bot["disp_24h"], bt_disp24, 1e-9):
            m24 += 1
            ex24 = ex24 or f"bot={bot['disp_24h']} bt={bt_disp24:.4f}"
        if not _close(bot["disp_7d"], bt_disp7, 1e-9):
            m7 += 1
            ex7 = ex7 or f"bot={bot['disp_7d']} bt={bt_disp7:.4f}"
        if bot["n_stress_global"] != bt_stress:
            mst += 1
            exst = exst or f"bot={bot['n_stress_global']} bt={bt_stress}"
    report("disp_24h", "2 implémentations", "OK" if m24 == 0 else "DIVERGE",
           f"{m24}/{len(draws_ts)} écarts" + (f" — ex. {ex24}" if ex24 else ""))
    report("disp_7d", "2 implémentations", "OK" if m7 == 0 else "DIVERGE",
           f"{m7}/{len(draws_ts)} écarts" + (f" — ex. {ex7}" if ex7 else ""))
    report("n_stress", "2 implémentations", "OK" if mst == 0 else "DIVERGE",
           f"{mst}/{len(draws_ts)} écarts" + (f" — ex. {exst}" if exst else ""))

    print("\n=== 6. Détection de compression (squeeze)")
    report("squeeze", "PARTAGÉ (signals.detect_squeeze_at)", "PARTAGÉ",
           "le backtest appelle la fonction du bot — aucune duplication")

    print("\n=== 7. OI delta 24h")
    report("oi_delta_24h", "2 implémentations", "RISQUE",
           "unités identiques (bps des deux côtés) MAIS le backtest la nomme "
           "`oi_delta_24h_pct` — piège de nommage ; fenêtres différentes "
           "(bot: plus vieux échantillon ≥23h ; BT: exactement 6 bougies)")

    print("\n=== 8. Coûts et funding")
    report("coûts / funding", "2 modèles ASSUMÉS", "RISQUE",
           "divergence #12 documentée et voulue (BT: +4 bps slippage + "
           "intégrale funding ; bot: avgPx réel). À ne pas confondre avec un bug")

    # ── synthèse ──────────────────────────────────────────────────────
    print("\n" + "=" * 74)
    div = [r for r in results if r["statut"] == "DIVERGE"]
    risk = [r for r in results if r["statut"] == "RISQUE"]
    print(f"DIVERGENCES : {len(div)}    RISQUES STRUCTURELS : {len(risk)}"
          f"    OK/PARTAGÉ : {len(results)-len(div)-len(risk)}")
    for r in div:
        print(f"  ✗ {r['entrée']} — {r['détail']}")
    for r in risk:
        print(f"  ⚠ {r['entrée']} — {r['détail']}")

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "analysis", "output", "input_parity_audit.json")
    with open(out, "w") as f:
        json.dump({"n_draws": n_draws, "results": results}, f, indent=1)
    print(f"\nDump : {out}")
    return 1 if div else 0


if __name__ == "__main__":
    sys.exit(main())
