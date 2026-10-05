"""Garde live/mirror — seuils d'arrêt pré-enregistrés (docs/mirror_halflife_2026_09.md § Seuils).

Lecture seule : calcule les quatre seuils et dit STOP ou OK. Ne met rien en
pause lui-même ; la consigne pré-enregistrée est d'appliquer le STOP sans débat.

    python3 -m alfred.tools.mirror_guard          # code de sortie 2 si STOP
"""
from __future__ import annotations

import glob
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BOTS = os.path.join(_REPO, "alfred", "data", "bots")

S2_MEAN_USD = -0.50      # écart d'exécution moyen par trade apparié
S2_WINDOW = 30           # derniers trades appariés (toutes époques)
S2_MIN_N = 20
S3_EPOCH_USD = -40.0     # écart réalisé live − mirror dans l'époque
S4_FORKS = 2             # re-synchronisations max sur 14 jours (au-delà → STOP)


def _backups(mirror_db):
    """Sauvegardes de fork (hors fichiers annexes SQLite -shm/-wal)."""
    return sorted(p for p in glob.glob(mirror_db + ".pre_fork_*")
                  if not p.endswith(("-shm", "-wal", "-journal")))


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _trades(path, since_iso):
    c = _ro(path)
    return {(r[0], r[1], r[2], r[3][:13]): r for r in c.execute(
        "SELECT symbol, strategy, direction, entry_time, exit_time, reason, pnl_usdt, size_usdt "
        "FROM trades WHERE exit_time >= ?", (since_iso,))}


def main():
    live_db = os.path.join(BOTS, "live", "bot.db")
    mirror_db = os.path.join(BOTS, "mirror", "bot.db")
    fork_ts = _ro(mirror_db).execute("SELECT MAX(ts) FROM events WHERE event='FORK'").fetchone()[0]
    if not fork_ts:
        print("aucun FORK dans mirror/bot.db — époque inconnue")
        return 1
    epoch = datetime.fromtimestamp(fork_ts, timezone.utc)
    since = epoch.isoformat()[:19]
    stops = []

    # S1 — divergence de noyau (LOGIC) dans l'époque
    hours = max(1, int((time.time() - fork_ts) / 3600) + 1)
    out = subprocess.run([sys.executable, "-m", "alfred.tools.compare_bots", "--a", "live", "--b", "mirror",
                          "--hours", str(hours)], cwd=_REPO, capture_output=True, text=True).stdout
    logic = sum(int(x) for x in re.findall(r"LOGIC=(\d+)", out)) + \
        sum(int(x) for x in re.findall(r"non appariées \(logic\)=(\d+)", out))
    print(f"S1 noyau      : {logic} divergence(s) LOGIC depuis {epoch:%m-%d %H:%M} → {'STOP' if logic else 'OK'}")
    if logic:
        stops.append("S1")

    # S2 — exécution sur les derniers trades appariés (époque courante + précédentes)
    pairs = []
    sources = [(mirror_db, since)] + [(p, "2026-09-27") for p in _backups(mirror_db)]
    seen = set()
    L = _trades(live_db, "2026-09-27")
    for path, s in sources:
        for k, m in _trades(path, s).items():
            l = L.get(k)
            if l and k not in seen and l[5] == m[5] and m[7]:
                seen.add(k)
                pairs.append((l[4], l[6] / l[7] * m[7] - m[6]))
    pairs.sort()
    last = [x for _, x in pairs[-S2_WINDOW:]]
    mean = sum(last) / len(last) if last else 0.0
    s2 = len(last) >= S2_MIN_N and mean <= S2_MEAN_USD
    print(f"S2 exécution  : {mean:+.2f} $/trade sur {len(last)} appariés (seuil {S2_MEAN_USD} $, n ≥ {S2_MIN_N}) "
          f"→ {'STOP' if s2 else 'OK'}")
    if s2:
        stops.append("S2")

    # S3 — écart réalisé dans l'époque
    gl = sum(r[6] for r in _trades(live_db, since).values())
    gm = sum(r[6] for r in _trades(mirror_db, since).values())
    s3 = (gl - gm) <= S3_EPOCH_USD
    print(f"S3 époque     : live {gl:+.2f} $ − mirror {gm:+.2f} $ = {gl - gm:+.2f} $ (seuil {S3_EPOCH_USD} $) "
          f"→ {'STOP' if s3 else 'OK'}")
    if s3:
        stops.append("S3")

    # S4 — fréquence des re-synchronisations (sauvegardes pre_fork datées)
    recent = [p for p in _backups(mirror_db)
              if time.time() - os.path.getmtime(p) <= 14 * 86400]
    s4 = len(recent) > S4_FORKS
    print(f"S4 fréquence  : {len(recent)} re-synchronisation(s) sur 14 j (max {S4_FORKS}) → {'STOP' if s4 else 'OK'}")
    if s4:
        stops.append("S4")

    print(f"\nVERDICT : {'STOP ' + '+'.join(stops) + ' — pause des entrées live (consigne pré-enregistrée)' if stops else 'OK'}")
    return 2 if stops else 0


if __name__ == "__main__":
    sys.exit(main())
