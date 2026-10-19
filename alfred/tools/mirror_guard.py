"""Garde live/mirror — seuils d'arrêt pré-enregistrés (docs/mirror_halflife_2026_09.md § Seuils).

Lecture seule : calcule les quatre seuils et dit STOP ou OK. Ne met rien en
pause lui-même ; la consigne pré-enregistrée est d'appliquer le STOP sans débat.

    python3 -m alfred.tools.mirror_guard              # code de sortie 2 si STOP
    python3 -m alfred.tools.mirror_guard --telegram   # cron : alerte si STOP ou contrôle impossible
    python3 -m alfred.tools.mirror_guard --test-alert # envoie un message de test

Muet quand tout est OK. Alerte Telegram (canal de live) si un seuil est franchi
OU si le contrôle n'a pas pu se faire : une garde qui plante ne doit pas se taire.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
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
    proc = subprocess.run([sys.executable, "-m", "alfred.tools.compare_bots", "--a", "live", "--b", "mirror",
                           "--hours", str(hours)], cwd=_REPO, capture_output=True, text=True)
    out = proc.stdout
    # compare_bots : 0 = tout attribué, 1 = divergences non attribuées (résultat
    # VALIDE, à compter), autre / pas de verdict = vrai échec. Sans ce garde-fou,
    # un compare_bots en panne lirait « 0 LOGIC » et dirait OK.
    if proc.returncode not in (0, 1) or "Verdict" not in out:
        raise RuntimeError(f"compare_bots en échec (code {proc.returncode}) : "
                           f"{(proc.stderr or out).strip()[-300:]}")
    # Amendement 1 (2026-10-19, docs/mirror_halflife_2026_09.md) : les écarts nés
    # de l'EXÉCUTION réelle sont comptés à part et ne déclenchent pas S1 —
    #   a) hard-stop exchange contre stop du bot sur le même trade ;
    #   b) même trade sorti par un stop des deux côtés, à un moment différent
    #      (niveau de stop décalé par le prix d'entrée réellement rempli).
    STOPS = {"catastrophe_stop", "exchange_stop"}
    lt, mt = _trades(live_db, since), _trades(mirror_db, since)
    iso = lambda s: datetime.fromisoformat(s).timestamp()
    exec_lines, logic_lines = [], []
    for line in out.splitlines():
        if "✗ [LOGIC" not in line:
            continue
        m = re.search(r"raison divergente (\S+) (\S+): live=(\S+) vs mirror=(\S+)", line)
        if m and m.group(3) != m.group(4) and {m.group(3), m.group(4)} <= STOPS:
            exec_lines.append(line.strip())
            continue
        m = re.search(r"close (live|mirror) sans pendant (?:live|mirror): (\S+) (\S+) (\S+) @ (\d\d-\d\d \d\d:\d\d)", line)
        if m and m.group(4) in STOPS:
            src, other = (lt, mt) if m.group(1) == "live" else (mt, lt)
            a = [r for r in src.values() if r[0] == m.group(2) and r[1] == m.group(3)
                 and r[4][5:16].replace("T", " ") == m.group(5)]
            if a and any(r[0] == a[0][0] and r[1] == a[0][1] and r[5] in STOPS
                         and abs(iso(r[3]) - iso(a[0][3])) <= 3600 for r in other.values()):
                exec_lines.append(line.strip())
                continue
        logic_lines.append(line.strip())
    logic = len(logic_lines)
    print(f"S1 noyau      : {logic} divergence(s) LOGIC depuis {epoch:%m-%d %H:%M} "
          f"(+ {len(exec_lines)} écart(s) d'exécution compté(s) à part) → {'STOP' if logic else 'OK'}")
    for l in logic_lines:
        print("     " + l)
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


def _send(text: str) -> bool:
    """Envoi synchrone sur le canal Telegram de live (cron : le script se
    termine aussitôt, un envoi en thread de fond serait perdu)."""
    from data_freshness import env_flag          # cron ne charge pas .env
    token, chat = env_flag("TG_BOT_TOKEN", ""), env_flag("TG_CHAT_ID", "")
    if not token or not chat:
        print("TG_BOT_TOKEN/TG_CHAT_ID absents — pas d'envoi", file=sys.stderr)
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",
                                    data=data, timeout=10) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception as e:
        print(f"envoi Telegram échoué : {e}", file=sys.stderr)
        return False


def run() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--test-alert", action="store_true")
    args = ap.parse_args()
    buf, err = io.StringIO(), None
    try:
        with redirect_stdout(buf):
            code = main()
    except Exception as e:
        code, err = 3, f"{type(e).__name__}: {e}"
    report = buf.getvalue() + (f"ERREUR : {err}\n" if err else "")
    print(report, end="")
    head = "🛡️ Garde live/mirror — "
    if args.test_alert:
        text = head + "message de test (contrôle quotidien planifié)\n\n" + report
    elif code == 2:
        text = (head + "STOP\n\n" + report + "\nConsigne pré-enregistrée : pause des ENTRÉES live "
                "(positions gardées, aucune liquidation). Reprise après attribution écrite.")
    elif code != 0:
        text = head + "contrôle IMPOSSIBLE\n\n" + report
    else:
        text = None
    if text and (args.telegram or args.test_alert):
        print("alerte Telegram envoyée" if _send(text) else "alerte Telegram NON envoyée")
    return code


if __name__ == "__main__":
    sys.exit(run())
