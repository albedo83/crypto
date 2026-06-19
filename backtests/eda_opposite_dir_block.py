"""Premise EDA — levier "autoriser LONG+SHORT simultanés sur le même coin".

Track 1 (btlive_compare) a montré que le plus gros trade manqué récurrent sur
senior/junior/paper est UNI S9 SHORT +71 bloqué par already_in_position pendant
qu'un UNI S5 LONG était tenu. Avant d'investir dans la ré-architecture risquée du
moteur (positions clé (coin,dir), ~15 sites + ré-iso-validation), on vérifie sur
28 mois si ce levier est MATÉRIEL ou juste le fluke UNI répété.

Méthode :
  1. run_window baseline 28m (aligned + margin + modulator) avec opp_block_log=[].
     Le hook logge tout signal de direction OPPOSÉE à une position détenue.
  2. Dédup réaliste : on n'ouvre qu'UNE position par apparition (busy-until =
     entry + hold), on ignore les signaux suivants pendant qu'elle "tient".
  3. Forward-walk PnL proxy par trade : entrée à l'open suivant, sortie au
     catastrophe-stop si touché sinon au timeout. Coûts ~14 bps RT. (Proxy : ignore
     prop_trail/traj_cut/dead_timeout — donne l'ordre de grandeur + le signe.)
  4. Verdict GO/STOP : combien de trades, PnL net total, concentration, par strat.

Proxy conservateur : les vrais smart-exits amélioreraient probablement les gagnants
(prop_trail) et limiteraient les perdants — donc un résultat positif au proxy est un
plancher crédible ; un résultat négatif/marginal = STOP franc.
"""
import datetime as dt

from backtests import backtest_rolling as br

COST_RT_BPS = br.COST + br.FUNDING_DRAG_BPS  # ~14 bps round-trip proxy
NOTIONAL = 500.0  # $ par trade (= cap notionnel), pour un chiffre $ comparable


def effective_stop_bps(strat: str, stop_bps) -> float:
    if stop_bps is not None:
        return float(stop_bps)
    if strat == "S8":
        return br.STOP_LOSS_S8
    return br.STOP_LOSS_BPS


def walk_pnl(coin_series, entry_idx, direction, hold_candles, stop_bps):
    """Forward-walk: stop d'abord, sinon timeout. Retourne net_bps ou None."""
    if entry_idx >= len(coin_series):
        return None
    entry = coin_series[entry_idx]["o"]
    if entry <= 0:
        return None
    stop_price = entry * (1 + direction * stop_bps / 1e4)
    last = min(entry_idx + hold_candles, len(coin_series) - 1)
    for i in range(entry_idx, last + 1):
        c = coin_series[i]
        if direction == 1 and c["l"] <= stop_price:
            gross = stop_bps
            return gross - COST_RT_BPS, "stop"
        if direction == -1 and c["h"] >= stop_price:
            gross = stop_bps
            return gross - COST_RT_BPS, "stop"
    exit_px = coin_series[last]["c"]
    gross = direction * (exit_px / entry - 1) * 1e4
    return gross - COST_RT_BPS, "timeout"


def main():
    print("Loading data...", flush=True)
    data = br.load_3y_candles()
    features = br.build_features(data)
    sector_features = br.compute_sector_features(features, data)
    dxy_data = br.load_dxy()
    oi_data = br.load_oi()
    funding_data = br.load_funding()

    latest_ts = max(c["t"] for c in data["BTC"])
    end_dt = dt.datetime.fromtimestamp(latest_ts / 1000, tz=dt.timezone.utc)
    start_dt = end_dt - dt.timedelta(days=28 * 30)  # ~28 mois
    start_ms = int(start_dt.timestamp() * 1000)
    print(f"Window {start_dt.date()} → {end_dt.date()}  (~28m)", flush=True)

    opp_log: list = []
    print("Running baseline run_window with opp_block_log...", flush=True)
    res = br.run_window(
        features, data, sector_features, dxy_data,
        start_ms, latest_ts, start_capital=1000.0,
        oi_data=oi_data, funding_data=funding_data,
        apply_adaptive_modulator=True, aligned=True, margin_check=True,
        opp_block_log=opp_log,
    )
    print(f"  baseline trades: {res['n_trades']}, PnL ${res['pnl']:+.0f}", flush=True)
    print(f"  raw opposite-direction signal occurrences logged: {len(opp_log)}", flush=True)

    # Dédup réaliste : busy-until par (coin, dir)
    opp_log.sort(key=lambda r: r["ts"])
    busy_until: dict = {}  # (coin,dir) -> ts (ms) jusqu'où la position "tient"
    opened = []
    for r in opp_log:
        key = (r["coin"], r["dir"])
        if r["ts"] < busy_until.get(key, 0):
            continue  # déjà "en position" sur ce coin+dir
        series = data[r["coin"]]
        if r["entry_idx"] >= len(series):
            continue
        stop_bps = effective_stop_bps(r["strat"], r["stop_bps"])
        res_walk = walk_pnl(series, r["entry_idx"], r["dir"], r["hold_candles"], stop_bps)
        if res_walk is None:
            continue
        net_bps, exit_kind = res_walk
        # busy jusqu'à l'exit (timeout ou stop ~ approx = hold)
        exit_idx = min(r["entry_idx"] + r["hold_candles"], len(series) - 1)
        busy_until[key] = series[exit_idx]["t"] if exit_idx < len(series) else r["ts"]
        opened.append({**r, "net_bps": net_bps, "exit_kind": exit_kind,
                       "pnl": NOTIONAL * net_bps / 1e4})

    n = len(opened)
    total_pnl = sum(o["pnl"] for o in opened)
    wins = sum(1 for o in opened if o["pnl"] > 0)
    print("\n================ PREMISE VERDICT ================")
    print(f"Opposite-dir trades capturable (dédup): {n}")
    if n == 0:
        print("  → AUCUN. STOP.")
        return
    yrs = (end_dt - start_dt).days / 365.25
    print(f"  cadence: {n/yrs:.1f}/an")
    print(f"  WR proxy: {wins}/{n} = {100*wins/n:.0f}%")
    print(f"  PnL net proxy total @${NOTIONAL:.0f} notionnel: ${total_pnl:+.0f}")
    print(f"  PnL moyen/trade: ${total_pnl/n:+.1f}  | médian bps: "
          f"{sorted(o['net_bps'] for o in opened)[n//2]:+.0f}")

    # Concentration : top 5 gagnants vs reste
    opened.sort(key=lambda o: -o["pnl"])
    top5 = sum(o["pnl"] for o in opened[:5])
    print(f"  concentration: top-5 trades = ${top5:+.0f} "
          f"({100*top5/total_pnl:.0f}% du total)" if total_pnl else "")
    print("  Top 5 gagnants:")
    for o in opened[:5]:
        d = dt.datetime.fromtimestamp(o["ts"]/1000, tz=dt.timezone.utc).date()
        print(f"    {d} {o['coin']:6} {o['strat']} {'L' if o['dir']==1 else 'S'} "
              f"(held {o['held_strat']} {'L' if o['held_dir']==1 else 'S'})  "
              f"${o['pnl']:+.0f}  {o['exit_kind']}")

    # Par stratégie du signal capturé
    from collections import defaultdict
    by_strat = defaultdict(lambda: [0, 0.0])
    for o in opened:
        by_strat[o["strat"]][0] += 1
        by_strat[o["strat"]][1] += o["pnl"]
    print("  Par stratégie (signal capturé):")
    for s, (cnt, p) in sorted(by_strat.items(), key=lambda x: -x[1][1]):
        print(f"    {s:4} n={cnt:3}  ${p:+.0f}")

    # Combien sont sur des coins/dates uniques (= pas juste UNI répété) ?
    coins = defaultdict(float)
    for o in opened:
        coins[o["coin"]] += o["pnl"]
    print(f"  coins distincts touchés: {len(coins)}")
    print("  Top 5 coins par PnL capturé:")
    for c, p in sorted(coins.items(), key=lambda x: -x[1])[:5]:
        print(f"    {c:6} ${p:+.0f}")


if __name__ == "__main__":
    main()
