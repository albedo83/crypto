"""Empreinte d'exécution — chaque run de backtest déclare ses conditions.

Motivation (2026-07-31) : trois incidents d'état implicite en une semaine —
carte sectorielle figée (v1.17.1), univers copié à la main (v1.17.2), baseline
mal étiquetée (v1.17.3, une référence annoncée « S5 à 3.0 » qui tournait à 1.0).
Trois visages du même défaut : **une mesure qui ne déclare pas ses conditions**.

Le correctif est le même que pour l'univers : rendre le défaut impossible à
commettre silencieusement. Chaque run imprime et persiste :

  - l'empreinte de la config **RÉSOLUE** (les valeurs après défauts et
    overrides, pas le fichier) — un `signal_mult` dormant change le hash ;
  - la révision git du code ;
  - l'horodatage du snapshot de données (dernière bougie + mtime des fichiers).

Le jour où deux runs « identiques » divergent, le diff des empreintes répond
avant qu'on ait posé la question.

Usage :
    from backtests.fingerprint import banner, fingerprint
    print(banner(params, slippage_bps=4.0))          # 3 lignes en tête de run
    fp = fingerprint(params, extra={"slippage": 4.0})  # dict à persister
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "output", "pairs_data")

# Champs dont la valeur est imprimée EN CLAIR dans le bandeau : ceux qui ont
# déjà causé une divergence silencieuse, ou qui changent la population de
# trades. Le hash couvre tout le reste.
_LOUD = ("signal_mult", "enabled_strategies", "max_notional_frac",
         "trade_blacklist", "strat_z")


def resolved_config(p) -> dict:
    """La config APRÈS défauts et overrides, normalisée pour être hashable."""
    out = {}
    for f in dataclasses.fields(p):
        v = getattr(p, f.name)
        if isinstance(v, (set, frozenset)):
            v = sorted(str(x) for x in v)
        elif isinstance(v, tuple):
            v = list(v)
        elif isinstance(v, dict):
            v = {str(k): v[k] for k in sorted(v, key=str)}
        out[f.name] = v
    return out


def config_hash(p) -> str:
    blob = json.dumps(resolved_config(p), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def git_rev() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=os.path.dirname(os.path.dirname(
                               os.path.abspath(__file__))),
                           capture_output=True, text=True, timeout=5)
        rev = r.stdout.strip() or "?"
        d = subprocess.run(["git", "status", "--porcelain"],
                           cwd=os.path.dirname(os.path.dirname(
                               os.path.abspath(__file__))),
                           capture_output=True, text=True, timeout=5)
        return rev + ("+dirty" if d.stdout.strip() else "")
    except Exception:
        return "?"


def data_snapshot(data: dict | None = None) -> dict:
    """Dernière bougie vue + mtime le plus récent des fichiers de données.

    Les fichiers sont rafraîchis toutes les 4h par cron : deux runs à une heure
    d'intervalle peuvent porter sur des données différentes. C'est précisément
    ce qui a fait passer un contrôle de non-régression pour un échec le 30/07.
    """
    snap = {"last_candle": None, "data_mtime": None, "n_symbols": None}
    if data:
        try:
            ts = max(c["t"] for rows in data.values() for c in rows[-1:])
            snap["last_candle"] = datetime.fromtimestamp(
                ts / 1000, timezone.utc).isoformat()[:16]
            snap["n_symbols"] = len(data)
        except Exception:
            pass
    try:
        mt = max(os.path.getmtime(os.path.join(DATA_DIR, f))
                 for f in os.listdir(DATA_DIR) if f.endswith(".json"))
        snap["data_mtime"] = datetime.fromtimestamp(
            mt, timezone.utc).isoformat()[:16]
    except Exception:
        pass
    return snap


def fingerprint(p, data: dict | None = None, extra: dict | None = None) -> dict:
    """Empreinte complète, à persister dans chaque dump JSON."""
    return {
        "config_hash": config_hash(p),
        "git_rev": git_rev(),
        "run_at": datetime.now(timezone.utc).isoformat()[:16],
        "data": data_snapshot(data),
        "loud": {k: resolved_config(p).get(k) for k in _LOUD},
        "extra": extra or {},
        "resolved_config": resolved_config(p),
    }


def banner(p, data: dict | None = None, extra: dict | None = None) -> str:
    """Trois lignes à imprimer en tête de run."""
    fp = fingerprint(p, data, extra)
    d = fp["data"]
    loud = " · ".join(
        f"{k}={fp['loud'][k]}" for k in ("signal_mult",) if fp["loud"].get(k))
    ex = " · ".join(f"{k}={v}" for k, v in (extra or {}).items())
    return (
        f"┌ config {fp['config_hash']} · git {fp['git_rev']} · "
        f"run {fp['run_at']}Z\n"
        f"│ données jusqu'au {d['last_candle']} "
        f"({d['n_symbols']} symboles, fichiers {d['data_mtime']}Z)\n"
        f"└ {loud}" + (f" · {ex}" if ex else ""))
