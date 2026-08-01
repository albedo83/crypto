"""Cadence du book HL en WS — ÉCOUTE PASSIVE, aucun ordre, aucune souscription privée."""
import asyncio, json, statistics, time
import websockets

COINS = ["BTC", "SOL", "GMX", "BLUR"]
DUR = 90

async def main():
    stats = {c: {"n": 0, "recv": [], "lag": [], "tob": 0, "last_tob": None,
                 "spread": []} for c in COINS}
    async with websockets.connect("wss://api.hyperliquid.xyz/ws",
                                  ping_interval=20) as ws:
        for c in COINS:
            await ws.send(json.dumps({"method": "subscribe",
                "subscription": {"type": "l2Book", "coin": c}}))
        t_end = time.time() + DUR
        last = {}
        while time.time() < t_end:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            except asyncio.TimeoutError:
                continue
            if m.get("channel") != "l2Book":
                continue
            d = m["data"]
            c = d["coin"]
            if c not in stats:
                continue
            now = time.time()
            s = stats[c]
            s["n"] += 1
            if c in last:
                s["recv"].append((now - last[c]) * 1000)
            last[c] = now
            if "time" in d:
                s["lag"].append(now * 1000 - d["time"])
            lv = d.get("levels") or []
            if len(lv) == 2 and lv[0] and lv[1]:
                bid, ask = float(lv[0][0]["px"]), float(lv[1][0]["px"])
                tob = (bid, ask)
                if s["last_tob"] is not None and tob != s["last_tob"]:
                    s["tob"] += 1
                s["last_tob"] = tob
                mid = (bid + ask) / 2
                if mid > 0:
                    s["spread"].append((ask - bid) / mid * 1e4)

    print(f"{'coin':6s} {'msg':>5s} {'msg/s':>6s} {'Δrecv méd':>10s} "
          f"{'Δrecv p90':>10s} {'lag méd':>9s} {'chg TOB/s':>10s} "
          f"{'spread méd':>11s} {'spread p10':>11s}")
    for c in COINS:
        s = stats[c]
        if not s["n"]:
            print(f"{c:6s} aucun message"); continue
        r = sorted(s["recv"]); sp = sorted(s["spread"])
        print(f"{c:6s} {s['n']:>5d} {s['n']/DUR:>6.2f} "
              f"{statistics.median(r) if r else 0:>9.0f}m "
              f"{r[int(.9*len(r))] if r else 0:>9.0f}m "
              f"{statistics.median(s['lag']) if s['lag'] else float('nan'):>8.0f}m "
              f"{s['tob']/DUR:>10.2f} "
              f"{statistics.median(sp) if sp else 0:>10.2f}b "
              f"{sp[int(.1*len(sp))] if sp else 0:>10.2f}b")
asyncio.run(main())
