"""EDA prémisse — un SHORT de TENDANCE a-t-il un edge (≠ nos fades mean-reversion) ?

Contexte : tous nos shorts (S5/S9/S10) sont des FADES d'une sur-extension HAUSSIÈRE.
Aucun ne shorte un alt qui CASSE à la baisse et CONTINUE de tomber (momentum-short).
L'EDA hold-duration a montré que le vrai ennemi du bot est le BTC DIRECTIONNEL ;
un short de tendance gagnerait précisément là. On teste la prémisse AVANT tout
moteur/sweep.

Méthode (lecture seule, sur candles brutes) : pour chaque (coin, bougie) on évalue
des conditions de BREAKDOWN, puis le rendement SHORT forward sur H bougies
(short_bps = -(close[i+H]/close[i]-1)*1e4 ; positif = le prix tombe).

GARDE-FOU DÉCISIF : les alts driftent à la baisse sur un échantillon bear → un short
inconditionnel paraît gagnant juste par le drift. On compare donc chaque setup au
BASELINE INCONDITIONNEL (même régime) : l'edge = conditionnel − baseline. Le setup
doit AJOUTER de l'edge, pas juste capter le drift. Et il doit passer le fee floor
(~12 bps RT ici, mémo « gross < ~50 bps = doomed »).

Segmentation par régime BTC 30d (bear < -5%, bull > +5%) — le bear est le contexte.

Usage : python3 -m backtests.eda_trend_short
"""
import statistics as st
import numpy as np

from backtests.backtest_genetic import load_3y_candles, TOKENS

COST_RT = 12.0          # bps round-trip (7 taker + 3 slip + 2 funding)
FLOOR = 50.0            # bar « net gross attendu > ~50 bps » sinon doomed
H_LIST = [6, 12, 24]    # horizons forward : 24h / 48h / 96h
LOOKBACK_30D = 180      # 180 bougies 4h = 30j


def main():
    print("Chargement candles 3 ans…")
    data = load_3y_candles()
    btc = data["BTC"]
    btc_c = {c["t"]: c["c"] for c in btc}
    btc_arr = [c["c"] for c in btc]
    btc_ix = {c["t"]: k for k, c in enumerate(btc)}

    def regime(ts):
        i = btc_ix.get(ts)
        if i is None or i < LOOKBACK_30D or btc_arr[i - LOOKBACK_30D] <= 0:
            return None
        r = (btc_arr[i] / btc_arr[i - LOOKBACK_30D] - 1) * 1e4
        return "bear" if r < -500 else ("bull" if r > 500 else "neutral")

    # Conditions de breakdown évaluées à l'index i d'un coin.
    # Chacune renvoie True/False. closes = array du coin.
    def conds(closes, i):
        out = {}
        # momentum baissier court / moyen
        if i >= 6:
            r24 = (closes[i] / closes[i - 6] - 1) * 1e4
            out["mom_24h<-300"] = r24 < -300
            out["mom_24h<-600"] = r24 < -600
        if i >= 42:
            r7d = (closes[i] / closes[i - 42] - 1) * 1e4
            out["mom_7d<-1000"] = r7d < -1000
            out["mom_7d<-2000"] = r7d < -2000
        # cassure de plus-bas (new low N bougies)
        for N in (12, 20, 30):
            if i >= N:
                out[f"new_low_{N}"] = closes[i] <= min(closes[i - N + 1:i + 1])
        # combo : new low 20 ET momentum 7d négatif (vrai breakdown soutenu)
        if i >= 42:
            r7d = (closes[i] / closes[i - 42] - 1) * 1e4
            out["newlow20&mom7d<-500"] = (closes[i] <= min(closes[i - 19:i + 1])) and r7d < -500
        return out

    # Collecte : pour chaque H, baseline + par-condition, segmenté régime
    # acc[H][cond][regime] = list of short_bps ; cond="__ALL__" = baseline
    acc = {H: {} for H in H_LIST}

    def push(H, cond, reg, val):
        acc[H].setdefault(cond, {}).setdefault(reg, []).append(val)
        acc[H][cond].setdefault("all", []).append(val)

    coins = [c for c in TOKENS if c in data]
    print(f"{len(coins)} coins. Scan…")
    for coin in coins:
        cl = [c["c"] for c in data[coin]]
        ts = [c["t"] for c in data[coin]]
        n = len(cl)
        for i in range(LOOKBACK_30D, n - max(H_LIST)):
            reg = regime(ts[i])
            if reg is None:
                continue
            cm = conds(cl, i)
            for H in H_LIST:
                if i + H >= n:
                    continue
                short_bps = -(cl[i + H] / cl[i] - 1) * 1e4
                push(H, "__ALL__", reg, short_bps)
                for cname, ok in cm.items():
                    if ok:
                        push(H, cname, reg, short_bps)

    def stats(vals):
        m = st.mean(vals)
        hit = 100 * sum(1 for v in vals if v > 0) / len(vals)
        return m, hit

    for H in H_LIST:
        hours = H * 4
        print(f"\n{'='*78}\n=== HORIZON {hours}h ({H} bougies) ===")
        base = acc[H]["__ALL__"]
        for reg in ("all", "bear", "neutral", "bull"):
            if reg not in base:
                continue
            m, hit = stats(base[reg])
            print(f"  BASELINE inconditionnel [{reg:<7}] n={len(base[reg]):<6} "
                  f"gross={m:+7.0f}  hit={hit:3.0f}%")
        print(f"  {'-'*72}")
        print(f"  {'condition':<26}{'rég':<8}{'n':>7}{'gross':>9}{'net':>8}"
              f"{'hit':>6}{'LIFT vs base':>14}")
        for cond in [c for c in acc[H] if c != "__ALL__"]:
            for reg in ("bear", "all"):  # focus bear (contexte) + global
                if reg not in acc[H][cond] or len(acc[H][cond][reg]) < 30:
                    continue
                m, hit = stats(acc[H][cond][reg])
                bm, _ = stats(base[reg])
                lift = m - bm
                net = m - COST_RT
                flag = "  <<<" if (net > FLOOR and lift > 30) else ""
                print(f"  {cond:<26}{reg:<8}{len(acc[H][cond][reg]):>7}"
                      f"{m:>+9.0f}{net:>+8.0f}{hit:>5.0f}%{lift:>+14.0f}{flag}")

    print("\nPRÉMISSE OK si une condition montre LIFT nettement >0 (ajoute de l'edge "
          "AU-DELÀ du drift baseline) ET net > ~50 bps, idéalement en bear. Sinon le "
          "short de tendance ne fait que capter le drift = pas un signal, et doomed par fees.")


if __name__ == "__main__":
    main()
