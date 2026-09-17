"""Read-only attribution of Live/Paper divergence; no broker or AI imports.

python3 audit_live_paper.py --data-dir /home/crypto/alfred/data \
    --json-out /tmp/live_paper.json --markdown-out /tmp/live_paper.md

Trades are matched by symbol, strategy, direction and UTC 4h entry period.
The decomposition is an accounting identity, NOT a causal IA scorecard.
Only closed trades contribute P&L. Open counterparts are reported separately.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3


UTC = timezone.utc


def timestamp(value):
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        result = dt.timestamp()
    if not math.isfinite(result):
        raise ValueError("Non-finite timestamp")
    return result


def iso(value):
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds")


def trade_key(trade):
    direction = trade["direction"]
    if direction in (1, -1):
        direction = "LONG" if direction == 1 else "SHORT"
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"Invalid direction: {direction!r}")
    return (trade["symbol"], trade["strategy"], direction,
            int(timestamp(trade["entry_time"]) // 14400))


def load_bot(data_dir, bot_id):
    directory = data_dir / "bots" / bot_id
    raw_state = (directory / "state.json").read_bytes()
    state = json.loads(raw_state)
    # mode=ro prevents accidental creation or mutation of the source DB.
    db = sqlite3.connect((directory / "bot.db").resolve().as_uri() + "?mode=ro",
                         uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        trades = [dict(r) for r in db.execute("SELECT * FROM trades ORDER BY entry_time")]
        last_event = db.execute("SELECT MAX(ts) FROM events").fetchone()[0]
        interventions = []
        for row in db.execute("SELECT ts,event,symbol,data FROM events WHERE "
                              "event IN ('ARBITER_DECISION','ARBITER_EXIT_DECISION') "
                              "ORDER BY ts"):
            data = json.loads(row["data"] or "{}")
            if data.get("acted") is True:
                interventions.append({"ts": row["ts"], "event": row["event"],
                                      "symbol": row["symbol"], "data": data})
    finally:
        db.close()
    if last_event is None:
        raise ValueError(f"{bot_id}: missing event coverage")
    return {"state": state, "trades": trades, "interventions": interventions,
            "last_event_ts": last_event,
            "state_sha256": hashlib.sha256(raw_state).hexdigest(),
            "db_path": str(directory / "bot.db")}


def cohort(bot, since, until):
    indexed = {}
    rows = list(bot["trades"])
    # Current open positions allow us to distinguish censoring from selection.
    rows.extend(dict(p, exit_time=None, pnl_usdt=0.0, reason="open")
                for p in bot["state"].get("positions", []))
    for row in rows:
        entry = timestamp(row["entry_time"])
        if not since <= entry <= until:
            continue
        size = float(row["size_usdt"])
        if not math.isfinite(size) or size <= 0:
            raise ValueError(f"Invalid notional for {row['symbol']}")
        key = trade_key(row)
        if key in indexed:
            raise ValueError(f"Ambiguous 4h match: {key}; no arbitrary pairing")
        closed = bool(row.get("exit_time")) and timestamp(row["exit_time"]) <= until
        pnl = float(row["pnl_usdt"]) if closed else 0.0
        if not math.isfinite(pnl):
            raise ValueError(f"Invalid P&L for {key}")
        indexed[key] = dict(row, size_usdt=size, closed=closed, pnl_usdt=pnl)
    return indexed


def decompose(paper, live):
    common = sorted(paper.keys() & live.keys(), key=lambda k: (k[3], k[:3]))
    pairs, pending = [], []
    for key in common:
        p, l = paper[key], live[key]
        if not (p["closed"] and l["closed"]):
            pending.append({"key": list(key), "paper_closed": p["closed"],
                            "live_closed": l["closed"],
                            "realized_delta_usd": l["pnl_usdt"] - p["pnl_usdt"]})
            continue
        rp, rl = p["pnl_usdt"] / p["size_usdt"], l["pnl_usdt"] / l["size_usdt"]
        size_effect = (l["size_usdt"] - p["size_usdt"]) * rp
        return_effect = l["size_usdt"] * (rl - rp)
        exit_lag = timestamp(l["exit_time"]) - timestamp(p["exit_time"])
        pairs.append({
            "key": list(key), "symbol": key[0], "strategy": key[1], "direction": key[2],
            "paper_entry": p["entry_time"], "live_entry": l["entry_time"],
            "paper_exit": p["exit_time"], "live_exit": l["exit_time"],
            "paper_reason": p["reason"], "live_reason": l["reason"],
            "paper_pnl_usd": p["pnl_usdt"], "live_pnl_usd": l["pnl_usdt"],
            "paper_size_usd": p["size_usdt"], "live_size_usd": l["size_usdt"],
            "entry_lag_seconds": timestamp(l["entry_time"]) - timestamp(p["entry_time"]),
            "exit_lag_seconds": exit_lag,
            "same_exit_reason": p["reason"] == l["reason"],
            "size_effect_usd": size_effect, "return_effect_usd": return_effect,
            "net_return_delta_bps": (rl - rp) * 10000,
        })
    unmatched = []
    for bot_id, own, other in (("paper", paper, live), ("live", live, paper)):
        for key in sorted(own.keys() - other.keys(), key=lambda k: (k[3], k[:3])):
            r = own[key]
            unmatched.append({"bot": bot_id, "key": list(key), "symbol": key[0],
                              "entry_time": r["entry_time"], "closed": r["closed"],
                              "pnl_usd": r["pnl_usdt"]})
    totals = {name: sum(r["pnl_usdt"] for r in rows.values())
              for name, rows in (("paper", paper), ("live", live))}
    attribution = {
        "size_effect_usd": sum(p["size_effect_usd"] for p in pairs),
        "return_effect_usd": sum(p["return_effect_usd"] for p in pairs),
        "unmatched_closed_delta_usd": sum(
            (1 if u["bot"] == "live" else -1) * u["pnl_usd"] for u in unmatched),
        "pending_pair_realized_delta_usd": sum(p["realized_delta_usd"] for p in pending),
    }
    delta = totals["live"] - totals["paper"]
    residual = delta - sum(attribution.values())
    if abs(residual) > 1e-8:
        raise ValueError(f"Attribution identity failed: residual={residual}")
    strategies = {}
    for name, rows in (("paper", paper), ("live", live)):
        groups = defaultdict(lambda: {"n": 0, "pnl_usd": 0.0})
        for key, r in rows.items():
            if r["closed"]:
                group = groups[f"{key[1]} {key[2]}"]
                group["n"] += 1
                group["pnl_usd"] += r["pnl_usdt"]
        strategies[name] = dict(groups)
    return {"closed_pnl_usd": totals, "live_minus_paper_usd": delta,
            "attribution": attribution, "identity_residual_usd": residual,
            "matched_closed": len(pairs), "pending_pairs": pending,
            "unmatched": unmatched, "pairs": pairs, "strategies": strategies}


def intervention_evidence(pair, interventions):
    entry = timestamp(pair["live_entry"])
    exit_ts = timestamp(pair["live_exit"])
    evidence = []
    for event in interventions:
        d = event["data"]
        target = d.get("entry_ts_ms")
        if target is None and d.get("entry_time"):
            target = timestamp(d["entry_time"]) * 1000
        if (event["symbol"] == pair["symbol"] and target is not None
                and abs(float(target) / 1000 - entry) < 1.0
                and entry <= event["ts"] + 1 < exit_ts + 1):
            evidence.append({"at": iso(event["ts"]), "event": event["event"],
                             "action": d.get("action", d.get("decision")),
                             "factor": d.get("factor"), "stop_usdt": d.get("stop_usdt"),
                             "prompt_hash": d.get("prompt_hash")})
    return evidence


def make_report(data_dir, since=None, until=None):
    bots = {name: load_bot(data_dir, name) for name in ("paper", "live")}
    if since is None:
        anchors = [b["state"].get("_perf_track_start_ts") for b in bots.values()]
        if not all(anchors):
            raise ValueError("Missing reset anchor; supply --since explicitly")
        since = max(timestamp(a) for a in anchors)
    until = min(b["last_event_ts"] for b in bots.values()) if until is None else until
    if since >= until:
        raise ValueError("Empty/reversed observation window")
    if any(until > b["last_event_ts"] for b in bots.values()):
        raise ValueError("Requested end exceeds one bot's event coverage")
    indexed = {name: cohort(b, since, until) for name, b in bots.items()}
    report = decompose(indexed["paper"], indexed["live"])
    report.update({"generated_utc": iso(datetime.now(UTC).timestamp()),
                   "since_utc": iso(since), "until_utc": iso(until),
                   "match_rule": "symbol,strategy,direction,UTC 4h entry period",
                   "source_snapshots": {name: {
                       "version": b["state"].get("version"),
                       "state_sha256": b["state_sha256"], "db_path": b["db_path"],
                       "last_event_utc": iso(b["last_event_ts"]),
                       "first_ledger_entry": min((r["entry_time"] for r in b["trades"]), default=None),
                   } for name, b in bots.items()},
                   "limits": [
                       "Read-only local snapshots per bot, not an atomic fleet snapshot or exchange reconciliation.",
                       "Cohort by entry date; closed P&L excludes unrealized positions and pre-cohort entries.",
                       "Available trades ledger only; no claim of complete pre-reset coverage.",
                       "Pairing is approximate within a 4h period; entry/exit lags are supplied.",
                       "Return component includes exits, fills, funding and accounting; it is NOT pure slippage.",
                       "Size decomposition uses paper return as reference; attribution order is explicit.",
                       "Unmatched P&L is descriptive, NOT recoverable profit or proof of a selection defect.",
                       "AI events are evidence of an intervention, not its causal portfolio P&L.",
                       "No significance test: correlated trades and repeated regimes are not independent draws.",
                   ]})
    for pair in report["pairs"]:
        pair["live_ai_interventions"] = intervention_evidence(pair, bots["live"]["interventions"])
    # Fixed operational milestones, not windows chosen to improve a result.
    report["milestones"] = {}
    for date in ("2026-08-29", "2026-09-06", "2026-09-16T17:47:40Z"):
        start = max(since, timestamp(date))
        if start < until:
            sub = decompose(*(cohort(bots[name], start, until) for name in ("paper", "live")))
            report["milestones"][date] = {k: sub[k] for k in (
                "closed_pnl_usd", "live_minus_paper_usd", "attribution", "matched_closed")}
    return report


def markdown(report):
    a = report["attribution"]
    lines = ["# Audit Live / Paper", "", f"Généré : {report['generated_utc']}",
             f"Cohorte d'entrées : {report['since_utc']} → {report['until_utc']}", "",
             "P&L réalisé uniquement ; les positions ouvertes ne sont pas valorisées ici.", "",
             "| Mesure | Valeur |", "|---|---:|",
             f"| P&L Paper | {report['closed_pnl_usd']['paper']:+.2f} $ |",
             f"| P&L Live | {report['closed_pnl_usd']['live']:+.2f} $ |",
             f"| Écart Live − Paper | {report['live_minus_paper_usd']:+.2f} $ |",
             f"| Trades clos appariés | {report['matched_closed']} |", "",
             "## Attribution comptable de l'écart", "",
             "| Composante | Live − Paper |", "|---|---:|",
             f"| Taille (rendement Paper de référence) | {a['size_effect_usd']:+.2f} $ |",
             f"| Rendement sur paires closes (taille Live) | {a['return_effect_usd']:+.2f} $ |",
             f"| Trades clos sans entrée appariée | {a['unmatched_closed_delta_usd']:+.2f} $ |",
             f"| Paires dont au moins une position reste ouverte | {a['pending_pair_realized_delta_usd']:+.2f} $ |",
             "", "Identité vérifiée : Δ = (L−P)×rP + L×(rL−rP), puis écarts hors paires closes.",
             "Le rendement regroupe sorties, exécution, funding et comptabilité ; ce n'est pas une attribution causale à l'IA.",
             "", "## Premières sorties divergentes", "",
             "| Symbole | Entrée Paper | Sortie Paper | Sortie Live | Événements IA agis liés |",
             "|---|---|---|---|---:|"]
    different = [p for p in report["pairs"] if not p["same_exit_reason"] or abs(p["exit_lag_seconds"]) > 120]
    for p in different[:12]:
        lines.append(f"| {p['symbol']} {p['strategy']} | {p['paper_entry']} | "
                     f"{p['paper_reason']} · {p['paper_exit']} | {p['live_reason']} · "
                     f"{p['live_exit']} | {len(p['live_ai_interventions'])} |")
    lines += ["", "## Jalons opérationnels", "",
              "| Entrées depuis | Paires closes | Effet taille | Effet rendement |",
              "|---|---:|---:|---:|"]
    for date, sub in report["milestones"].items():
        lines.append(f"| {date} | {sub['matched_closed']} | "
                     f"{sub['attribution']['size_effect_usd']:+.2f} $ | "
                     f"{sub['attribution']['return_effect_usd']:+.2f} $ |")
    lines += ["", "## Limites", ""] + [f"- {s}" for s in report["limits"]]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent / "alfred/data")
    parser.add_argument("--since", type=timestamp)
    parser.add_argument("--until", type=timestamp)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    args = parser.parse_args()
    # Refuse reports inside the observed store (state/DB must stay read-only).
    outputs = [p for p in (args.json_out, args.markdown_out) if p is not None]
    if len({p.resolve() for p in outputs}) != len(outputs):
        parser.error("Output paths must be distinct")
    for path in outputs:
        if path.resolve().is_relative_to(args.data_dir.resolve()):
            parser.error("Reports must be outside the observed data directory")
    report = make_report(args.data_dir, args.since, args.until)
    rendered = markdown(report)
    if args.json_out:
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.markdown_out:
        args.markdown_out.write_text(rendered)
    print(rendered)


if __name__ == "__main__":
    main()
