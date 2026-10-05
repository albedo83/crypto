"""Test « à un euro » : hedger le bêta BTC du book Alfred (2026).

Book = trades du backtest canonique ALIGNED (même moteur que live/paper),
2026-01-01 → fin des données, capital 1000 $.
Chaque jour (clôture 00:00 UTC) :
  exposition bêta = Σ dir × notionnel × β_coin  (β glissant 60 j sur rendements 4h)
  hedge BTC = −exposition bêta de la veille (retaillé chaque jour)
  P&L hedge = hedge × rendement BTC du jour − 4,5 bps × |Δhedge| (taker)
              − funding (short BTC encaisse un funding positif)
Comparaison avec / sans hedge : P&L, DD max, Calmar, par trimestre.
Critère fixé avant : le hedge est retenu s'il améliore le Calmar sur les 3
trimestres de 2026 sans réduire le P&L total de plus de 10 %.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_rolling import load_dxy, load_funding, load_oi, run_window
from backtests.backtest_sector import compute_sector_features

DAY = 86400000
TAKER_BPS = 4.5


def main():
    data = load_3y_candles()
    f = build_features(data)
    sec = compute_sector_features(f, data)
    end = max(c["t"] for c in data["BTC"])
    start = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    r = run_window(f, data, sec, load_dxy(), start, end, start_capital=1000.0,
                   oi_data=load_oi(), funding_data=load_funding(),
                   apply_adaptive_modulator=True, aligned=True, margin_check=True)
    trades = r["trades"]
    print(f"Backtest 2026 : {len(trades)} trades, P&L {r['pnl_pct']:+.1f} %, DD {r['max_dd_pct']:.1f} %")

    # Rendements 4h et bêta glissant 60 j (360 bougies), sans regard futur
    ts = np.array([c["t"] for c in data["BTC"]])
    btc = np.array([c["c"] for c in data["BTC"]], dtype=float)
    bret = np.diff(btc) / btc[:-1]
    beta = {}
    for coin, cs in data.items():
        if coin == "BTC":
            continue
        px = {c["t"]: c["c"] for c in cs}
        a = np.array([px.get(t, np.nan) for t in ts], dtype=float)
        ar = np.diff(a) / a[:-1]
        b = np.full(len(ts), np.nan)
        for i in range(361, len(ts)):
            x, y = bret[i - 361:i - 1], ar[i - 361:i - 1]
            ok = ~(np.isnan(x) | np.isnan(y))
            if ok.sum() > 100:
                b[i] = np.cov(x[ok], y[ok])[0, 1] / np.var(x[ok])
        beta[coin] = dict(zip(ts, b))

    # Jours, P&L journalier du book (réalisé à la sortie + variation latente)
    days = np.arange(start // DAY * DAY, end // DAY * DAY + DAY, DAY)
    close_at = {}
    for i, t in enumerate(ts):
        close_at[(t + 4 * 3600000) // DAY * DAY] = i  # dernière bougie close dans le jour
    btc_day = np.array([btc[close_at[d]] if d in close_at else np.nan for d in days])
    px_coin = {coin: {c["t"]: c["c"] for c in cs} for coin, cs in data.items()}

    def mark(coin, d):
        i = close_at.get(d)
        return px_coin[coin].get(ts[i]) if i is not None else None

    book = np.zeros(len(days))
    expo = np.zeros(len(days))       # exposition bêta à la clôture du jour
    for t in trades:
        e_t, x_t, coin, dr, size = t["entry_t"], t["exit_t"], t["coin"], t["dir"], t["size"]
        prev = px_coin[coin].get(e_t)
        acc = 0.0
        for k, d in enumerate(days):
            de = d + DAY                     # clôture du jour d
            if de <= e_t or de > x_t:
                continue                     # jour non entièrement couvert
            m = mark(coin, d)
            if m is None:
                continue
            if prev:
                inc = dr * size * (m / prev - 1)
                book[k] += inc
                acc += inc
            prev = m
            bcoin = beta[coin].get(ts[close_at[d]], np.nan)
            if not np.isnan(bcoin):
                expo[k] += dr * size * bcoin
        # le jour de sortie reçoit le résidu → la somme = P&L réel du trade
        book[np.searchsorted(days, x_t // DAY * DAY)] += t["pnl"] - acc

    fund = load_funding().get("BTC")
    hedge_pnl = np.zeros(len(days))
    prev_h = 0.0
    for k in range(1, len(days)):
        h = -expo[k - 1]
        if np.isnan(btc_day[k]) or np.isnan(btc_day[k - 1]):
            continue
        ret = btc_day[k] / btc_day[k - 1] - 1
        cost = abs(h - prev_h) * TAKER_BPS / 1e4
        fcost = 0.0
        if fund is not None:
            ft, fr = np.asarray(fund[0]), np.asarray(fund[1])
            m = (ft > days[k - 1] + DAY) & (ft <= days[k] + DAY)
            fcost = h * fr[m].sum()          # long paie un taux positif, short le reçoit
        hedge_pnl[k] = h * ret - cost - fcost
        prev_h = h

    def report(label, pnl):
        eq = 1000 + np.cumsum(pnl)
        peak = np.maximum.accumulate(eq)
        dd = ((eq - peak) / peak).min() * 100
        tot = eq[-1] - 1000
        calmar = (tot / 1000 * 100) / abs(dd) if dd < 0 else float("inf")
        return tot, dd, calmar

    print(f"\n{'période':<22}{'sans hedge':>28}{'avec hedge':>28}")
    for lo, hi, lab in ((0, len(days), "2026 complet"),) + tuple(
            (np.searchsorted(days, int(datetime(2026, m, 1, tzinfo=timezone.utc).timestamp() * 1000)),
             np.searchsorted(days, int(datetime(2026, m + 3, 1, tzinfo=timezone.utc).timestamp() * 1000)) if m < 10 else len(days),
             f"T{(m + 2) // 3} 2026") for m in (1, 4, 7)):
        a = report("", book[lo:hi])
        b = report("", (book + hedge_pnl)[lo:hi])
        print(f"{lab:<22}{a[0]:>+9.0f}$ DD{a[1]:>6.1f}% C{a[2]:>5.2f}{b[0]:>+9.0f}$ DD{b[1]:>6.1f}% C{b[2]:>5.2f}")
    eq = 1000 + np.cumsum(book)
    ddv = (eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)
    br = np.nan_to_num(np.r_[0, np.diff(btc_day) / btc_day[:-1]])
    for thr in (0.05, 0.10):
        msk = ddv < -thr
        if msk.sum() > 5:
            print(f"Creux > {thr:.0%} : {msk.sum()} jours — corr P&L book/BTC {np.corrcoef(book[msk], br[msk])[0, 1]:+.2f}"
                  f" — hedge sur ces jours {hedge_pnl[msk].sum():+.0f} $")
    k = int(np.argmin(ddv)); j = int(np.argmax(eq[:k + 1]))
    print(f"Pire creux {days[j]//1000:.0f}→{days[k]//1000:.0f} (epoch s) : book {book[j+1:k+1].sum():+.0f} $, "
          f"hedge {hedge_pnl[j+1:k+1].sum():+.0f} $, corr {np.corrcoef(book[j+1:k+1], br[j+1:k+1])[0, 1]:+.2f}")
    print(f"\nExposition bêta moyenne : {np.mean(expo):+.0f} $ (|moy| {np.mean(np.abs(expo)):.0f} $) — "
          f"corrélation P&L book / rendement BTC : "
          f"{np.corrcoef(book[1:], np.nan_to_num(np.diff(btc_day) / btc_day[:-1]))[0, 1]:+.2f}")


if __name__ == "__main__":
    main()

