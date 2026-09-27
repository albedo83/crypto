"""Forke l'état complet d'un bot vers un autre — T0 d'une expérience de miroir.

Copie `state.json` (positions, cooldowns, signal_first_seen, borne de scan 4 h,
pic, tampon du frein equity) ET `bot.db` (historique de trades et d'events), de
sorte que le bot cible soit un clone exact du bot source à l'instant du fork.

Protocole : `docs/mirror_halflife_2026_09.md` — re-synchroniser à CHAQUE
divergence constatée.

Trois garde-fous, parce qu'un T0 approximatif ne mesure rien :
  1. la cible doit être en mode `paper` dans bots.json — jamais d'écrasement
     d'un bot qui engage de l'argent réel ;
  2. Alfred doit être ARRÊTÉ — copier un SQLite en cours d'écriture donne une
     base tronquée, et l'état copié serait incohérent avec les positions ;
  3. un état cible existant n'est écrasé qu'avec --force, et il est sauvegardé.

Usage:
    python3 -m alfred.tools.fork_bot_state --from live --to mirror [--force]
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _bot_dir(bot_id: str) -> str:
    return os.path.join(_REPO, "alfred", "data", "bots", bot_id)


def _mode_of(bot_id: str) -> str | None:
    with open(os.path.join(_REPO, "alfred", "bots.json")) as fh:
        cfg = json.load(fh)
    for b in cfg.get("bots", []):
        if b.get("id") == bot_id:
            return b.get("mode")
    return None


def _alfred_running() -> bool:
    r = subprocess.run(["pgrep", "-f", r"python3 -m alfred$"],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def main() -> int:
    src = dst = None
    force = "--force" in sys.argv
    if "--from" in sys.argv:
        src = sys.argv[sys.argv.index("--from") + 1]
    if "--to" in sys.argv:
        dst = sys.argv[sys.argv.index("--to") + 1]
    if not src or not dst:
        print(__doc__)
        return 2

    if _mode_of(dst) != "paper":
        print(f"REFUS : le bot cible '{dst}' n'est pas en mode paper "
              f"(mode={_mode_of(dst)!r}). On ne forke jamais sur de l'argent réel.")
        return 1
    if _alfred_running():
        print("REFUS : Alfred tourne. Arrêter le process avant de forker "
              "(copie SQLite en cours d'écriture = base tronquée).")
        return 1

    s_dir, d_dir = _bot_dir(src), _bot_dir(dst)
    s_state, s_db = os.path.join(s_dir, "state.json"), os.path.join(s_dir, "bot.db")
    for p in (s_state, s_db):
        if not os.path.exists(p):
            print(f"REFUS : source introuvable : {p}")
            return 1
    os.makedirs(d_dir, exist_ok=True)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    for name in ("state.json", "bot.db"):
        target = os.path.join(d_dir, name)
        if os.path.exists(target):
            if not force:
                print(f"REFUS : {target} existe déjà — relancer avec --force "
                      f"(l'existant sera sauvegardé).")
                return 1
            shutil.copy2(target, f"{target}.pre_fork_{stamp}")
        shutil.copy2(os.path.join(s_dir, name), target)

    # Journaliser le fork DANS la base de la cible : le T0 devient auditable.
    with open(os.path.join(d_dir, "state.json")) as fh:
        st = json.load(fh)
    db = sqlite3.connect(os.path.join(d_dir, "bot.db"))
    db.execute("INSERT INTO events (ts, event, symbol, data) VALUES (?,?,?,?)",
               (int(time.time()), "FORK", None,
                json.dumps({"from": src, "to": dst, "stamp": stamp,
                            "positions": len(st.get("positions") or []),
                            "capital": st.get("capital"),
                            "total_pnl": st.get("total_pnl"),
                            "protocol": "docs/mirror_halflife_2026_09.md"})))
    db.commit()
    db.close()

    print(f"✓ fork {src} → {dst} : {len(st.get('positions') or [])} positions, "
          f"capital ${st.get('capital')}, P&L réalisé ${st.get('total_pnl'):.2f}")
    print(f"  event FORK écrit dans {dst}/bot.db — T0 auditable")
    print(f"  démarrer Alfred pour charger le clone (T0 = ce démarrage)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
