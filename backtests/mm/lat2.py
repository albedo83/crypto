"""RTT HL, connexion PERSISTANTE — lectures publiques uniquement, aucun ordre."""
import http.client, json, statistics, time

def probe(payload, n=80, pause=0.25):
    c = http.client.HTTPSConnection("api.hyperliquid.xyz", timeout=10)
    body = json.dumps(payload)
    hdr = {"Content-Type": "application/json"}
    t0 = time.perf_counter(); c.request("POST", "/info", body, hdr); c.getresponse().read()
    handshake = (time.perf_counter() - t0) * 1000
    lat = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            c.request("POST", "/info", body, hdr)
            c.getresponse().read()
            lat.append((time.perf_counter() - t0) * 1000)
        except Exception as e:
            print("  reconnect:", e)
            c = http.client.HTTPSConnection("api.hyperliquid.xyz", timeout=10)
        time.sleep(pause)
    return handshake, sorted(lat)

for name, p in (("allMids", {"type": "allMids"}),
                ("l2Book BTC", {"type": "l2Book", "coin": "BTC"}),
                ("l2Book GMX", {"type": "l2Book", "coin": "GMX"})):
    hs, l = probe(p)
    print(f"{name:12s} 1er appel (TLS) {hs:6.1f} ms | keep-alive n={len(l)}  "
          f"med {statistics.median(l):6.1f}  p90 {l[int(.9*len(l))]:6.1f}  "
          f"p99 {l[min(int(.99*len(l)),len(l)-1)]:6.1f}  min {l[0]:5.1f}", flush=True)
