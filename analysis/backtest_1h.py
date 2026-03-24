"""1h Candle Test — Do our signals work at higher resolution?

Fetch 1h candles from Hyperliquid (~208 days), adapt features,
test existing signals + new 1h-specific patterns.

Usage:
    python3 -m analysis.backtest_1h
"""

from __future__ import annotations

import json, os, time, random, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "output", "pairs_data")
os.makedirs(DATA_DIR, exist_ok=True)

TOKENS = [
    "ARB", "OP", "AVAX", "SUI", "APT", "SEI", "NEAR",
    "AAVE", "MKR", "COMP", "SNX", "PENDLE", "DYDX",
    "DOGE", "WLD", "BLUR", "LINK", "PYTH",
    "SOL", "INJ", "CRV", "LDO", "STX", "GMX",
    "IMX", "SAND", "GALA", "MINA",
]
REF = ["BTC", "ETH"]
SECTORS = {
    "L1": ["SOL", "AVAX", "SUI", "APT", "NEAR", "SEI"],
    "DeFi": ["AAVE", "MKR", "CRV", "SNX", "PENDLE", "COMP", "DYDX", "LDO"],
    "Gaming": ["GALA", "IMX", "SAND"],
    "Infra": ["LINK", "PYTH", "STX", "INJ", "ARB", "OP"],
    "Meme": ["DOGE", "WLD", "BLUR", "MINA"],
}
TOKEN_SECTOR = {}
for s, toks in SECTORS.items():
    for t in toks:
        TOKEN_SECTOR[t] = s

COST_BPS = 14.0  # 12 + 2 for leverage
LEVERAGE = 2.0
SIZE = 250.0
MAX_POS = 6
MAX_DIR = 4


def fetch_1h(coin):
    """Fetch 1h candles from Hyperliquid."""
    cache = os.path.join(DATA_DIR, f"{coin}_1h_recent.json")
    if os.path.exists(cache) and (time.time() - os.path.getmtime(cache)) < 3600:
        with open(cache) as f:
            return json.load(f)

    end_ts = int(time.time() * 1000)
    start_ts = end_ts - 220 * 86400 * 1000
    try:
        payload = json.dumps({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": "1h", "startTime": start_ts, "endTime": end_ts
        }}).encode()
        req = urllib.request.Request("https://api.hyperliquid.xyz/info",
                                     data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read())
        if raw:
            with open(cache, "w") as f:
                json.dump(raw, f)
        return raw or []
    except Exception as e:
        print(f"  {coin} 1h fetch failed: {e}")
        return []


def load_all_1h():
    """Load 1h candles for all tokens."""
    data = {}
    for coin in TOKENS + REF:
        raw = fetch_1h(coin)
        if len(raw) < 200:
            continue
        candles = [{"t": c["t"], "o": float(c["o"]), "c": float(c["c"]),
                     "h": float(c["h"]), "l": float(c["l"]),
                     "v": float(c.get("v", 0))} for c in raw]
        data[coin] = candles
        time.sleep(0.15)
    return data


def build_1h_features(data):
    """Build features at 1h resolution.

    Adapted from 4h features:
    - ret_24h: 24 candles (= 1 day, was ret_6h on 4h)
    - ret_168h: 168 candles (= 7 days, was ret_42h on 4h)
    - ret_336h: 336 candles (= 14 days)
    - vol_7d: std over 168 candles
    - vol_ratio: 7d vol / 14d vol
    - range_pct: current candle
    - vol_z: volume z-score
    - btc_7d, btc_30d
    - alt_index: mean 7d return
    - sector divergence
    """
    btc_candles = data.get("BTC", [])
    btc_closes = np.array([c["c"] for c in btc_candles]) if btc_candles else np.array([])

    # Alt returns at each timestamp for cross-alt features
    all_alt_ret = defaultdict(dict)
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        closes = np.array([c["c"] for c in candles])
        for i in range(168, len(candles)):
            if closes[i-168] > 0:
                ret = (closes[i] / closes[i-168] - 1) * 1e4
                all_alt_ret[candles[i]["t"]][coin] = ret

    features = {}
    for coin in TOKENS:
        if coin not in data:
            continue
        candles = data[coin]
        closes = np.array([c["c"] for c in candles])
        volumes = np.array([c["v"] for c in candles])
        n = len(candles)
        coin_feats = []

        for i in range(max(720, 1), n):  # need 30 days = 720 candles warmup
            c = candles[i]
            f = {"t": c["t"], "_idx": i}

            # Returns
            if closes[i-168] > 0:
                f["ret_168h"] = (closes[i] / closes[i-168] - 1) * 1e4
            else:
                continue
            if i >= 24 and closes[i-24] > 0:
                f["ret_24h"] = (closes[i] / closes[i-24] - 1) * 1e4
            else:
                f["ret_24h"] = 0
            if i >= 336 and closes[i-336] > 0:
                f["ret_336h"] = (closes[i] / closes[i-336] - 1) * 1e4
            else:
                f["ret_336h"] = 0

            # Volatility
            rets_7d = np.diff(closes[i-168:i+1]) / closes[i-168:i]
            f["vol_7d"] = float(np.std(rets_7d) * 1e4) if len(rets_7d) > 1 else 0

            if i >= 336:
                rets_14d = np.diff(closes[i-336:i+1]) / closes[i-336:i]
                f["vol_14d"] = float(np.std(rets_14d) * 1e4) if len(rets_14d) > 1 else 0
            else:
                f["vol_14d"] = f["vol_7d"]

            f["vol_ratio"] = f["vol_7d"] / f["vol_14d"] if f["vol_14d"] > 0 else 1.0

            # Range
            f["range_pct"] = (c["h"] - c["l"]) / c["c"] * 1e4 if c["c"] > 0 else 0

            # Volume z
            if i >= 720:
                vol_window = volumes[i-720:i]
                vm = float(np.mean(vol_window))
                vs = float(np.std(vol_window))
                f["vol_z"] = (volumes[i] - vm) / vs if vs > 0 else 0
            else:
                f["vol_z"] = 0

            # BTC features (find closest BTC candle)
            btc_idx = None
            for bi in range(len(btc_candles)-1, -1, -1):
                if btc_candles[bi]["t"] <= c["t"]:
                    btc_idx = bi
                    break
            if btc_idx and btc_idx >= 720 and btc_closes[btc_idx-720] > 0:
                f["btc_30d"] = (btc_closes[btc_idx] / btc_closes[btc_idx-720] - 1) * 1e4
            elif btc_idx and btc_idx >= 168 and btc_closes[btc_idx-168] > 0:
                f["btc_30d"] = (btc_closes[btc_idx] / btc_closes[btc_idx-168] - 1) * 1e4
            else:
                f["btc_30d"] = 0

            if btc_idx and btc_idx >= 168 and btc_closes[btc_idx-168] > 0:
                f["btc_7d"] = (btc_closes[btc_idx] / btc_closes[btc_idx-168] - 1) * 1e4
            else:
                f["btc_7d"] = 0

            # Alt index
            alt_rets = all_alt_ret.get(c["t"], {})
            f["alt_index"] = float(np.mean(list(alt_rets.values()))) if alt_rets else 0

            # Sector divergence
            sector = TOKEN_SECTOR.get(coin)
            if sector and len(alt_rets) > 3:
                peers = [r for co, r in alt_rets.items()
                         if co != coin and TOKEN_SECTOR.get(co) == sector]
                if len(peers) >= 2:
                    f["sector_div"] = f["ret_168h"] - float(np.mean(peers))
                else:
                    f["sector_div"] = 0
            else:
                f["sector_div"] = 0

            coin_feats.append(f)

        features[coin] = coin_feats

    return features


def backtest_1h(features, data, config):
    """Backtest on 1h candles."""
    hold = config.get("hold", 72)  # in 1h candles
    signals_fn = config.get("signals_fn")
    label = config.get("label", "")

    coins = [c for c in TOKENS if c in features and c in data]

    # Split: train first 70%, test last 30%
    all_ts = set()
    for coin in coins:
        for f in features[coin]:
            all_ts.add(f["t"])
    sorted_ts = sorted(all_ts)
    split_idx = int(len(sorted_ts) * 0.7)
    train_end = sorted_ts[split_idx] if split_idx < len(sorted_ts) else sorted_ts[-1]

    feat_by_ts = defaultdict(dict)
    for coin in coins:
        for f in features[coin]:
            feat_by_ts[f["t"]][coin] = f

    coin_by_ts = {}
    for coin in coins:
        coin_by_ts[coin] = {}
        for i, c in enumerate(data[coin]):
            coin_by_ts[coin][c["t"]] = i

    positions = {}
    trades = []
    cooldown = {}

    for ts in sorted_ts:
        # Exits
        for coin in list(positions.keys()):
            pos = positions[coin]
            if coin not in coin_by_ts or ts not in coin_by_ts[coin]: continue
            ci = coin_by_ts[coin][ts]
            held = ci - pos["idx"]
            if held <= 0: continue
            if held >= hold:
                current = data[coin][ci]["c"]
                if current > 0:
                    gross = pos["dir"]*(current/pos["entry"]-1)*1e4*LEVERAGE
                    net = gross - COST_BPS
                    trades.append({"pnl": SIZE*net/1e4, "net": net,
                                   "dir": pos["dir"], "strat": pos["strat"],
                                   "coin": coin, "entry_t": pos["entry_t"], "exit_t": ts})
                del positions[coin]
                cooldown[coin] = ts + 6*3600*1000  # 6h cooldown

        # Entries
        n_long = sum(1 for p in positions.values() if p["dir"]==1)
        n_short = sum(1 for p in positions.values() if p["dir"]==-1)

        candidates = signals_fn(ts, feat_by_ts.get(ts, {}), data, coin_by_ts)

        candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
        seen = set()
        for cand in candidates:
            coin = cand["coin"]
            if coin in seen or coin in positions: continue
            if coin in cooldown and ts < cooldown[coin]: continue
            seen.add(coin)
            if len(positions) >= MAX_POS: break
            if cand["dir"]==1 and n_long>=MAX_DIR: continue
            if cand["dir"]==-1 and n_short>=MAX_DIR: continue

            f = feat_by_ts.get(ts, {}).get(coin)
            if not f: continue
            idx = f["_idx"]
            if idx+1 >= len(data[coin]): continue
            entry = data[coin][idx+1]["o"]
            if entry <= 0: continue

            positions[coin] = {"dir": cand["dir"], "entry": entry,
                "idx": idx+1, "entry_t": data[coin][idx+1]["t"],
                "strat": cand["strat"]}
            if cand["dir"]==1: n_long+=1
            else: n_short+=1

    if not trades:
        return {"label": label, "pnl": 0, "n": 0}

    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    avg = float(np.mean([t["net"] for t in trades]))
    wins = sum(1 for t in trades if t["net"]>0)
    train_pnl = sum(t["pnl"] for t in trades if t["entry_t"]<train_end)
    test_pnl = sum(t["pnl"] for t in trades if t["entry_t"]>=train_end)

    by_strat = defaultdict(lambda: {"n": 0, "pnl": 0})
    for t in trades:
        by_strat[t["strat"]]["n"] += 1
        by_strat[t["strat"]]["pnl"] += t["pnl"]

    return {"label": label, "pnl": round(pnl,0), "n": n, "avg": round(avg,1),
            "win": round(wins/n*100,0), "train": round(train_pnl,0),
            "test": round(test_pnl,0), "by_strat": dict(by_strat)}


def main():
    print("=" * 70)
    print("  1H CANDLE TEST")
    print("  Do our signals work at higher resolution?")
    print("=" * 70)

    print("\nFetching 1h candles...")
    data = load_all_1h()
    print(f"Loaded {len(data)} tokens")

    for coin in ["BTC", "SOL", "DOGE"]:
        if coin in data:
            n = len(data[coin])
            days = n / 24
            print(f"  {coin}: {n} candles ({days:.0f} days)")

    print("\nBuilding 1h features...")
    t0 = time.time()
    features = build_1h_features(data)
    total = sum(len(v) for v in features.values())
    print(f"Built {total:,} feature rows in {time.time()-t0:.1f}s")

    # ═══════════════════════════════════════════════════════════
    # Test existing signals adapted to 1h
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"  EXISTING SIGNALS on 1h")
    print(f"{'='*70}")

    def signals_s1(ts, feats, data, cbt):
        """S1: btc_30d > 2000 → LONG"""
        cands = []
        for coin, f in feats.items():
            if coin in ["BTC","ETH"]: continue
            if f.get("btc_30d", 0) > 2000:
                cands.append({"coin": coin, "dir": 1, "strat": "S1", "score": abs(f["btc_30d"])})
        return cands

    def signals_s2(ts, feats, data, cbt):
        """S2: alt_index < -1000 → LONG"""
        cands = []
        for coin, f in feats.items():
            if coin in ["BTC","ETH"]: continue
            if f.get("alt_index", 0) < -1000:
                cands.append({"coin": coin, "dir": 1, "strat": "S2", "score": abs(f["alt_index"])})
        return cands

    def signals_s4(ts, feats, data, cbt):
        """S4: vol contraction → SHORT"""
        cands = []
        for coin, f in feats.items():
            if coin in ["BTC","ETH"]: continue
            if f.get("vol_ratio", 2) < 1.0 and f.get("range_pct", 999) < 50:  # tighter for 1h
                cands.append({"coin": coin, "dir": -1, "strat": "S4", "score": (1-f["vol_ratio"])*1000})
        return cands

    def signals_s5(ts, feats, data, cbt):
        """S5: sector divergence → FOLLOW"""
        cands = []
        for coin, f in feats.items():
            if coin in ["BTC","ETH"]: continue
            if abs(f.get("sector_div", 0)) >= 1000 and f.get("vol_z", 0) >= 1.0:
                d = 1 if f["sector_div"] > 0 else -1
                cands.append({"coin": coin, "dir": d, "strat": "S5", "score": abs(f["sector_div"])})
        return cands

    def signals_all(ts, feats, data, cbt):
        return signals_s1(ts,feats,data,cbt) + signals_s2(ts,feats,data,cbt) + \
               signals_s4(ts,feats,data,cbt) + signals_s5(ts,feats,data,cbt)

    # New 1h-specific signals
    def signals_momentum_1d(ts, feats, data, cbt):
        """NEW: 24h momentum → continue"""
        cands = []
        for coin, f in feats.items():
            if coin in ["BTC","ETH"]: continue
            ret = f.get("ret_24h", 0)
            if abs(ret) > 300:  # 3% in 24h
                d = 1 if ret > 0 else -1
                cands.append({"coin": coin, "dir": d, "strat": "Mom24h", "score": abs(ret)})
        return cands

    def signals_vol_spike(ts, feats, data, cbt):
        """NEW: volume spike → follow direction"""
        cands = []
        for coin, f in feats.items():
            if coin in ["BTC","ETH"]: continue
            if f.get("vol_z", 0) > 3.0:  # extreme volume
                ret = f.get("ret_24h", 0)
                if abs(ret) > 100:  # with directional move
                    d = 1 if ret > 0 else -1
                    cands.append({"coin": coin, "dir": d, "strat": "VolSpike", "score": f["vol_z"]*100})
        return cands

    def signals_btc_dip_1h(ts, feats, data, cbt):
        """NEW: BTC drops 3%+ in 7d → LONG alts"""
        cands = []
        for coin, f in feats.items():
            if coin in ["BTC","ETH"]: continue
            if f.get("btc_7d", 0) < -300:
                cands.append({"coin": coin, "dir": 1, "strat": "BtcDip", "score": abs(f["btc_7d"])})
        return cands

    def signals_all_plus_new(ts, feats, data, cbt):
        return signals_all(ts,feats,data,cbt) + signals_momentum_1d(ts,feats,data,cbt) + \
               signals_vol_spike(ts,feats,data,cbt) + signals_btc_dip_1h(ts,feats,data,cbt)

    configs = [
        # Existing signals, various holds
        {"label": "S1 only, hold 72h", "signals_fn": signals_s1, "hold": 72},
        {"label": "S2 only, hold 72h", "signals_fn": signals_s2, "hold": 72},
        {"label": "S4 only, hold 72h", "signals_fn": signals_s4, "hold": 72},
        {"label": "S5 only, hold 48h", "signals_fn": signals_s5, "hold": 48},
        {"label": "All existing, hold 72h", "signals_fn": signals_all, "hold": 72},
        {"label": "All existing, hold 24h", "signals_fn": signals_all, "hold": 24},
        {"label": "All existing, hold 12h", "signals_fn": signals_all, "hold": 12},
        # New 1h signals
        {"label": "Mom 24h, hold 24h", "signals_fn": signals_momentum_1d, "hold": 24},
        {"label": "Mom 24h, hold 12h", "signals_fn": signals_momentum_1d, "hold": 12},
        {"label": "Vol spike, hold 24h", "signals_fn": signals_vol_spike, "hold": 24},
        {"label": "Vol spike, hold 12h", "signals_fn": signals_vol_spike, "hold": 12},
        {"label": "BTC dip 1h, hold 72h", "signals_fn": signals_btc_dip_1h, "hold": 72},
        {"label": "BTC dip 1h, hold 24h", "signals_fn": signals_btc_dip_1h, "hold": 24},
        # Combined
        {"label": "ALL signals, hold 24h", "signals_fn": signals_all_plus_new, "hold": 24},
        {"label": "ALL signals, hold 48h", "signals_fn": signals_all_plus_new, "hold": 48},
    ]

    print(f"\n  {'Config':<35} {'P&L':>7} {'N':>5} {'Avg':>6} {'W%':>4} {'Trn':>7} {'Tst':>7}")
    print(f"  {'-'*75}")

    for cfg in configs:
        r = backtest_1h(features, data, cfg)
        v = "✓" if r.get("train",0)>0 and r.get("test",0)>0 else ""
        print(f"  {r['label']:<35} ${r['pnl']:>+6} {r['n']:>4} {r.get('avg',0):>+5.1f} "
              f"{r.get('win',0):>3}% ${r.get('train',0):>+6} ${r.get('test',0):>+6} {v}")

        if r.get("by_strat"):
            for sn, sv in sorted(r["by_strat"].items()):
                print(f"    {sn}: ${sv['pnl']:>+6.0f} ({sv['n']}t)")


if __name__ == "__main__":
    main()
