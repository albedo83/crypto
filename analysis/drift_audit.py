"""One-shot drift audit: compare bot.gross_pnl trade-by-trade vs HL.closedPnl.

Reads 95 live trades from reversal_ticks.db, fetches HL fills (90d window),
matches close-side fills per trade, and dumps a sorted contribution table to
backtests/drift_audit_live.md. No live impact, no VERSION bump, no restart.
"""
import json
import os
import sqlite3
import datetime as dt
from urllib.request import Request, urlopen

DB_PATH = '/home/crypto/analysis/output_live/reversal_ticks.db'
ENV_PATH = '/home/crypto/.env'
OUT_MD   = '/home/crypto/backtests/drift_audit_live.md'

def load_env():
    env = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env

def post_hl(body):
    req = Request("https://api.hyperliquid.xyz/info",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urlopen(req, timeout=15))

def main():
    env = load_env()
    from eth_account import Account
    addr = Account.from_key(env['HL_PRIVATE_KEY']).address
    print(f"Live wallet: {addr}")

    # 1) Read all 95 bot trades from DB
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = c.execute(
        "SELECT symbol, strategy, direction, entry_time, exit_time, "
        "       entry_price, exit_price, size_usdt, gross_bps, net_bps, "
        "       pnl_usdt, funding_usdt, reason "
        "FROM trades ORDER BY exit_time"
    ).fetchall()
    conn.close()
    print(f"Loaded {len(rows)} bot trades.")

    # 2) Fetch all HL fills on 90d window
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    start_ms = now_ms - 90 * 86400 * 1000
    fills = post_hl({"type": "userFillsByTime", "user": addr,
                     "startTime": start_ms, "endTime": now_ms})
    print(f"Fetched {len(fills)} HL fills.")

    # 3) For each bot trade, find matching CLOSE fills
    # HL fills have: coin, side ("A"=ask/sell, "B"=bid/buy), px, sz, fee,
    # closedPnl (str), time (ms), dir (descriptive e.g. "Close Long"), crossed.
    # A close-side fill has dir starting with "Close" OR closedPnl != "0.0".
    # Matching: same coin, time within [entry_time, exit_time + slack].
    audit = []
    SLACK_S = 300  # 5 minute slack on exit timestamp matching
    for row in rows:
        (sym, strat, direction, entry_t, exit_t, ep, xp, size, gbps,
         nbps, pnl_usdt, fund_usdt, reason) = row
        e_ms = int(dt.datetime.fromisoformat(entry_t.replace('Z', '+00:00')).timestamp() * 1000)
        x_ms = int(dt.datetime.fromisoformat(exit_t.replace('Z', '+00:00')).timestamp() * 1000)
        bot_gross = size * gbps / 1e4
        bot_net = size * nbps / 1e4

        # Find close-side fills: same coin, time within entry_ms..exit_ms+slack,
        # AND fill dir is a closing direction OR closedPnl is non-zero.
        # Match by accounting: sum closedPnl of fills whose timestamp falls in
        # [exit_ms - slack, exit_ms + slack] AND coin matches.
        close_fills = [f for f in fills
                       if f.get('coin') == sym
                       and abs(int(f['time']) - x_ms) <= SLACK_S * 1000
                       and float(f.get('closedPnl', 0)) != 0]
        # Sum HL's closedPnl for these fills (HL excludes fees from closedPnl)
        hl_close_pnl = sum(float(f['closedPnl']) for f in close_fills)
        hl_fees = sum(float(f.get('fee', 0)) for f in close_fills)
        hl_sz_total = sum(float(f.get('sz', 0)) for f in close_fills)
        hl_n_fills = len(close_fills)

        delta_gross = bot_gross - hl_close_pnl
        audit.append({
            'symbol': sym, 'strat': strat, 'dir': direction,
            'entry_t': entry_t[:19], 'exit_t': exit_t[:19],
            'size_usdt': size, 'gross_bps': gbps, 'net_bps': nbps,
            'bot_gross': bot_gross, 'bot_net': bot_net,
            'bot_pnl_usdt': pnl_usdt, 'bot_funding': fund_usdt,
            'hl_close_pnl': hl_close_pnl, 'hl_fees': hl_fees,
            'hl_n_fills': hl_n_fills, 'hl_sz': hl_sz_total,
            'delta_gross': delta_gross, 'reason': reason,
            'entry_price': ep, 'exit_price': xp,
        })

    # 4) Stats
    matched = [a for a in audit if a['hl_n_fills'] > 0]
    unmatched = [a for a in audit if a['hl_n_fills'] == 0]
    total_bot_gross = sum(a['bot_gross'] for a in audit)
    total_hl_pnl = sum(a['hl_close_pnl'] for a in matched)
    total_delta = sum(a['delta_gross'] for a in matched)
    print(f"\n  Matched: {len(matched)}/{len(audit)} trades")
    print(f"  Unmatched (no HL close fills found): {len(unmatched)}")
    print(f"  Total bot gross: ${total_bot_gross:+.2f}")
    print(f"  Total HL close_pnl: ${total_hl_pnl:+.2f}")
    print(f"  Total delta (bot - HL): ${total_delta:+.2f}")

    # 5) Top contributors
    audit_sorted = sorted(audit, key=lambda a: abs(a['delta_gross']), reverse=True)
    print(f"\n  Top 15 trades by |delta|:")
    for a in audit_sorted[:15]:
        print(f"    {a['symbol']:6s} {a['strat']:3s} {'LONG' if a['dir']==1 else 'SHORT':5s}  "
              f"size=${a['size_usdt']:>6.1f} bot_gross=${a['bot_gross']:+7.2f} "
              f"hl_pnl=${a['hl_close_pnl']:+7.2f} delta=${a['delta_gross']:+6.2f} "
              f"n_fills={a['hl_n_fills']} reason={a['reason']}")

    # Per-token aggregation
    print(f"\n  Per-token aggregated delta:")
    per_token = {}
    for a in matched:
        s = a['symbol']
        per_token.setdefault(s, [0.0, 0])
        per_token[s][0] += a['delta_gross']
        per_token[s][1] += 1
    for sym, (delta, n) in sorted(per_token.items(), key=lambda x: abs(x[1][0]), reverse=True)[:15]:
        print(f"    {sym:6s}  n={n:3d}  total_delta=${delta:+7.2f}  avg=${delta/n:+6.3f}")

    # 6) Write markdown report
    lines = []
    lines.append("# Equity drift audit — trade par trade (live)\n")
    lines.append(f"_Generated 2026-05-16. Wallet `{addr}`. Window 90d (HL fills cutoff)._\n")
    lines.append("## TL;DR\n")
    lines.append(f"- Trades bot dans la fenêtre : **{len(audit)}**")
    lines.append(f"- Trades matched avec close-fills HL : **{len(matched)}**")
    lines.append(f"- Trades unmatched (close fills introuvables) : **{len(unmatched)}**")
    lines.append(f"- **Bot tracked gross PnL** : `${total_bot_gross:+.2f}`")
    lines.append(f"- **HL closed_pnl sum (matched)** : `${total_hl_pnl:+.2f}`")
    lines.append(f"- **Δ total (bot − HL)** : **`${total_delta:+.2f}`** ← le drift recherché\n")
    lines.append(f"Décomposition vs alerte EQUITY_DRIFT actuelle :")
    lines.append(f"- Drift sur fills (gross discrepancy) : `${total_delta:+.2f}`")
    lines.append(f"- Funding diff (bot $-3.53 vs HL $-4.01) : `+$0.48`")
    lines.append(f"- Fees diff (bot $11.48 estimé vs HL $10.56 réel) : `+$0.92`")
    lines.append(f"- Total expliqué : `${total_delta + 0.48 + 0.92:+.2f}` (vs alerte `+$6.88`)\n")

    lines.append("## Top 20 trades par |Δ| absolu\n")
    lines.append("| Symbol | Strat | Dir | Size $ | bot_gross | HL closedPnl | Δ | n_fills | Reason |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---|")
    for a in audit_sorted[:20]:
        dr = 'LONG' if a['dir']==1 else 'SHORT'
        lines.append(f"| {a['symbol']} | {a['strat']} | {dr} | "
                     f"{a['size_usdt']:.1f} | {a['bot_gross']:+.2f} | "
                     f"{a['hl_close_pnl']:+.2f} | **{a['delta_gross']:+.2f}** | "
                     f"{a['hl_n_fills']} | {a['reason']} |")

    lines.append("\n## Per-token aggregation\n")
    lines.append("| Symbol | n trades | Σ delta | avg / trade |")
    lines.append("|---|---:|---:|---:|")
    for sym, (delta, n) in sorted(per_token.items(), key=lambda x: abs(x[1][0]), reverse=True):
        lines.append(f"| {sym} | {n} | **{delta:+.2f}** | {delta/n:+.3f} |")

    lines.append("\n## Unmatched trades (no HL close fills found within ±5min)\n")
    if unmatched:
        lines.append("| Symbol | Strat | Dir | Exit time | bot_gross | Reason |")
        lines.append("|---|---|---|---|---:|---|")
        for a in unmatched:
            dr = 'LONG' if a['dir']==1 else 'SHORT'
            lines.append(f"| {a['symbol']} | {a['strat']} | {dr} | {a['exit_t']} | "
                         f"{a['bot_gross']:+.2f} | {a['reason']} |")
    else:
        lines.append("_Tous les trades ont matched des fills HL. Aucun trade fantôme._")

    # Partial-fill analysis: trades with multiple HL fills on close
    multi_fill = [a for a in matched if a['hl_n_fills'] > 1]
    lines.append("\n## Trades avec multi-fills à la clôture (partial fills)\n")
    lines.append(f"- **{len(multi_fill)}** trades sur {len(matched)} ont eu &gt;1 fill HL à la clôture.")
    if multi_fill:
        delta_multi = sum(a['delta_gross'] for a in multi_fill)
        delta_single = sum(a['delta_gross'] for a in matched if a['hl_n_fills'] == 1)
        lines.append(f"- Σ Δ sur multi-fill : `${delta_multi:+.2f}` ({len(multi_fill)} trades, avg `${delta_multi/len(multi_fill):+.3f}`/trade)")
        n_single = len(matched) - len(multi_fill)
        if n_single > 0:
            lines.append(f"- Σ Δ sur single-fill : `${delta_single:+.2f}` ({n_single} trades, avg `${delta_single/n_single:+.3f}`/trade)")

    with open(OUT_MD, 'w') as f:
        f.write('\n'.join(lines))
    print(f"\nReport written: {OUT_MD}")

if __name__ == "__main__":
    main()
