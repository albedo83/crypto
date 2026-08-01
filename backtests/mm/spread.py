"""Snapshot de spreads et de profondeur — LECTURES PUBLIQUES. Aucun ordre."""
import json, statistics, sys, time, urllib.request
sys.path.insert(0, "/home/crypto")
from alfred.settings import DEFAULT_PARAMS as P

def q(p):
    r = urllib.request.Request("https://api.hyperliquid.xyz/info",
        data=json.dumps(p).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=25))

meta, ctxs = q({"type": "metaAndAssetCtxs"})
names = [u["name"] for u in meta["universe"]]
vol = {}
for n, c in zip(names, ctxs):
    if n in P.trade_symbols:
        vol[n] = float(c.get("dayNtlVlm", 0))
ranked = sorted(vol, key=vol.get, reverse=True)
strata = {"liquide": ranked[:4], "moyen": ranked[len(ranked)//2-2:len(ranked)//2+2],
          "fin": ranked[-4:]}
print("strates (volume notionnel 24 h) :")
for k, v in strata.items():
    print(f"  {k:8s} " + " ".join(f"{s}(${vol[s]/1e6:.1f}M)" for s in v))

coins = [c for v in strata.values() for c in v]
acc = {c: {"sp": [], "d0b": [], "d0a": [], "d5": []} for c in coins}
N = 20
for i in range(N):
    for c in coins:
        try:
            b = q({"type": "l2Book", "coin": c})
        except Exception:
            continue
        lv = b.get("levels") or []
        if len(lv) != 2 or not lv[0] or not lv[1]:
            continue
        bid, ask = float(lv[0][0]["px"]), float(lv[1][0]["px"])
        mid = (bid + ask) / 2
        if mid <= 0:
            continue
        a = acc[c]
        a["sp"].append((ask - bid) / mid * 1e4)
        a["d0b"].append(float(lv[0][0]["sz"]) * bid)
        a["d0a"].append(float(lv[1][0]["sz"]) * ask)
        lo, hi = mid * (1 - 5e-4), mid * (1 + 5e-4)
        a["d5"].append(
            sum(float(x["sz"]) * float(x["px"]) for x in lv[0] if float(x["px"]) >= lo) +
            sum(float(x["sz"]) * float(x["px"]) for x in lv[1] if float(x["px"]) <= hi))
    time.sleep(2)

print(f"\n{'coin':7s} {'strate':9s} {'vol24h':>9s} {'spread méd':>11s} {'sp p10':>8s} "
      f"{'sp p90':>8s} {'1er niv $':>11s} {'±5bps $':>11s} {'demi-sp − 1.5bps':>17s}")
for k, v in strata.items():
    for c in v:
        a = acc[c]
        if not a["sp"]:
            print(f"{c:7s} {k:9s} pas de données"); continue
        sp = sorted(a["sp"])
        med = statistics.median(sp)
        d0 = statistics.median([min(x, y) for x, y in zip(a["d0b"], a["d0a"])])
        d5 = statistics.median(a["d5"])
        print(f"{c:7s} {k:9s} {vol[c]/1e6:>8.1f}M {med:>10.2f}b "
              f"{sp[int(.1*len(sp))]:>7.2f}b {sp[int(.9*len(sp))]:>7.2f}b "
              f"{d0:>10,.0f} {d5:>10,.0f} {med/2 - 1.5:>+16.2f}b")
