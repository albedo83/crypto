"""RTT vers l'API HL — LECTURES PUBLIQUES UNIQUEMENT, aucun ordre."""
import json, statistics, time, urllib.request

URL = "https://api.hyperliquid.xyz/info"

def probe(payload, n=60, pause=0.35):
    lat = []
    body = json.dumps(payload).encode()
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(URL, data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
            lat.append((time.perf_counter() - t0) * 1000)
        except Exception as e:
            print("  ERR", e)
        time.sleep(pause)
    return lat

for name, payload in (("meta", {"type": "meta"}),
                      ("allMids", {"type": "allMids"}),
                      ("l2Book BTC", {"type": "l2Book", "coin": "BTC"})):
    l = probe(payload)
    l.sort()
    print(f"{name:12s} n={len(l):3d}  med {statistics.median(l):7.1f} ms  "
          f"p90 {l[int(.90*len(l))]:7.1f}  p99 {l[min(int(.99*len(l)),len(l)-1)]:7.1f}  "
          f"min {l[0]:6.1f}  max {l[-1]:7.1f}", flush=True)
