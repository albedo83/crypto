"""bbo vs l2Book vs trades — ÉCOUTE PASSIVE. Aucun ordre."""
import asyncio, json, statistics, time
import websockets

COINS = ["BTC", "SOL", "GMX", "BLUR"]
DUR = 120

async def main():
    st = {}
    def slot(ch, c):
        return st.setdefault((ch, c), {"n": 0, "iv": [], "lag": [], "last": None})
    async with websockets.connect("wss://api.hyperliquid.xyz/ws",
                                  ping_interval=20, max_queue=None) as ws:
        for c in COINS:
            for t in ("bbo", "l2Book", "trades"):
                await ws.send(json.dumps({"method": "subscribe",
                    "subscription": {"type": t, "coin": c}}))
        t_end = time.time() + DUR
        while time.time() < t_end:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            except asyncio.TimeoutError:
                continue
            ch = m.get("channel")
            d = m.get("data")
            now = time.time() * 1000
            if ch == "bbo":
                s = slot("bbo", d["coin"]); ts = d.get("time")
            elif ch == "l2Book":
                s = slot("l2Book", d["coin"]); ts = d.get("time")
            elif ch == "trades":
                if not d: continue
                s = slot("trades", d[0]["coin"])
                ts = max(t_["time"] for t_ in d)
            else:
                continue
            s["n"] += 1
            if ts: s["lag"].append(now - ts)
            if s["last"]: s["iv"].append(now - s["last"])
            s["last"] = now

    print(f"{'flux':8s} {'coin':6s} {'msg':>6s} {'msg/s':>7s} {'Δ méd':>8s} "
          f"{'Δ p10':>8s} {'Δ p90':>8s} {'lag méd':>9s} {'lag p90':>9s}")
    for (ch, c), s in sorted(st.items()):
        iv = sorted(s["iv"]); lg = sorted(s["lag"])
        f = lambda a, q: a[min(int(q*len(a)), len(a)-1)] if a else float('nan')
        print(f"{ch:8s} {c:6s} {s['n']:>6d} {s['n']/DUR:>7.2f} "
              f"{statistics.median(iv) if iv else float('nan'):>7.0f}m "
              f"{f(iv,.1):>7.0f}m {f(iv,.9):>7.0f}m "
              f"{statistics.median(lg) if lg else float('nan'):>8.0f}m "
              f"{f(lg,.9):>8.0f}m")
asyncio.run(main())
