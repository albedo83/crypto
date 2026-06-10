"""A4 — export des candles de market.db vers le format pairs_data des
backtests. Même source de données pour le bot et le BT (exigence rupture).

Le format cible est celui de backtests/output/pairs_data/{SYM}_4h_3y.json :
liste de dicts HL {t, T, s, i, o, c, h, l, v, n} avec valeurs STR pour
o/c/h/l/v (héritage HL REST — les loaders BT font float()).

Usage :
    python3 -m alfred.tools.export_candles                # tous les symboles
    python3 -m alfred.tools.export_candles BTC ETH        # sélection
    python3 -m alfred.tools.export_candles --merge        # fusionne avec les
        pairs_data existants (l'export ne couvre que la fenêtre Alfred ;
        --merge préserve le deep-history pré-Alfred fetché par REST)

Sans --merge : écrit {SYM}_4h_alfred.json à côté (non destructif).
Avec --merge : met à jour {SYM}_4h_3y.json en unionnant par t (les closes
Alfred priment — elles sont auditées WS-vs-REST en continu).
"""

from __future__ import annotations

import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)

MARKET_DB = os.path.join(_REPO, "alfred", "data", "market.db")
PAIRS_DIR = os.path.join(_REPO, "backtests", "output", "pairs_data")


def export_symbol(db, symbol: str, merge: bool) -> tuple[int, str]:
    rows = db.conn.execute(
        """SELECT t, close_t, o, h, l, c, v, n FROM candles
           WHERE symbol=? AND interval='4h' AND closed=1 ORDER BY t""",
        (symbol,)).fetchall()
    if not rows:
        return 0, "no_data"
    out = [{"t": r[0], "T": r[1], "s": symbol, "i": "4h",
            "o": str(r[2]), "c": str(r[5]), "h": str(r[3]), "l": str(r[4]),
            "v": str(r[6]), "n": r[7]} for r in rows]

    if merge:
        path = os.path.join(PAIRS_DIR, f"{symbol}_4h_3y.json")
        existing = []
        if os.path.exists(path):
            with open(path) as fh:
                existing = json.load(fh)
        by_t = {c["t"]: c for c in existing}
        by_t.update({c["t"]: c for c in out})       # Alfred closes priment
        merged = [by_t[t] for t in sorted(by_t)]
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(merged, fh)
        os.replace(tmp, path)
        return len(out), f"merged → {len(merged)} total"
    else:
        path = os.path.join(PAIRS_DIR, f"{symbol}_4h_alfred.json")
        with open(path, "w") as fh:
            json.dump(out, fh)
        return len(out), "standalone"


def main() -> int:
    import sqlite3
    merge = "--merge" in sys.argv
    syms = [a for a in sys.argv[1:] if not a.startswith("--")]
    db_path = MARKET_DB
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    class _RO:
        pass
    db = _RO()
    db.conn = conn

    if not syms:
        syms = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM candles WHERE closed=1 ORDER BY symbol")]
    if not syms:
        print("Aucune candle close en DB — Alfred n'a pas encore accumulé de données")
        return 1

    os.makedirs(PAIRS_DIR, exist_ok=True)
    total = 0
    for sym in syms:
        n, note = export_symbol(db, sym, merge)
        total += n
        print(f"  {sym:6} : {n:5} candles ({note})")
    print(f"\nTotal : {total} candles exportées ({'merge' if merge else 'standalone'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
