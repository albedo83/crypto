"""FRAÎCHEUR DES ENTRÉES — la plomberie a enfin un surveillant.

Motivation (2026-08-02, 7ᵉ incident silencieux et **le premier découvert par
accident**) : la base OI de recherche s'arrêtait au 2026-06-29 depuis cinq
semaines. Personne ne l'a vu, parce que personne ne regardait. La supervision
en place surveille les **performances** (`strategy_review.py`) et les
**mesures** (`measure_guards.py`, `fingerprint.py`) — pas les **tuyaux**.

Ce module donne à chaque source de données un **âge maximal**. Dépassement ⇒
anomalie critique remontée dans la revue quotidienne de l'auditeur IA.

Deux statuts, et la distinction est le cœur du dispositif :

  · **STALE**  — la source devrait être fraîche et ne l'est pas. Anomalie.
  · **INVALID** — horodatage aberrant (futur, ou vieux de siècles) : unité mal
    déclarée. Attrapé sur ce module lui-même au premier run.
  · **FROZEN** — la source est arrêtée pour une raison **connue et déclarée**,
    avec sa date. Ce n'est pas une anomalie, c'est un fait consigné.

Sans le second statut, une source définitivement morte hurlerait tous les jours
jusqu'à ce qu'on cesse de lire les alertes — c'est-à-dire jusqu'au 8ᵉ incident.

Stdlib uniquement : importable par les sentinelles comme par n'importe quel
script d'analyse.

Usage :
    python3 -m data_freshness            # tableau lisible
    from data_freshness import check_all, critical
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.abspath(__file__))


@dataclass
class Source:
    name: str
    path: str
    max_age_h: float | None          # None ⇒ pas d'attente de fraîcheur
    kind: str = "sqlite"             # sqlite | file | filedir
    query: str = ""                  # SELECT MAX(<col>) FROM <table>
    unit: str = "s"                  # unité de l'horodatage : s | ms
    note: str = ""
    frozen_since: str = ""           # AAAA-MM-JJ ⇒ statut FROZEN, pas STALE
    frozen_reason: str = ""
    suffix: str = ".json"            # pour kind=filedir
    only_traded: bool = False        # filedir : ne garder que Params.trade_symbols


SOURCES: list[Source] = [
    # ── chemins CHAUDS : le live en dépend, l'alerte doit être bruyante ──
    Source("market_snapshots (télémétrie live OI/funding/premium)",
           "alfred/data/market.db", 3.0,
           query="SELECT MAX(ts) FROM market_snapshots", unit="s",
           note="écrit par MarketDataMaster à chaque poll REST"),
    Source("candles 4h (store canonique du bot)",
           "alfred/data/market.db", 8.0,
           query="SELECT MAX(t) FROM candles", unit="ms",
           note="bougies 4h persistées ; 8 h = une bougie + marge"),
    Source("funding_hourly (live)",
           "alfred/data/market.db", 3.0,
           query="SELECT MAX(ts) FROM funding_hourly", unit="ms"),
    Source("ticks (live)",
           "alfred/data/market.db", 3.0,
           query="SELECT MAX(ts) FROM ticks", unit="s"),

    # ── chemins TIÈDES : la recherche en dépend, pas le live ──
    # ⚠ Un contrôle au niveau du DOSSIER prend le max des mtime : les bougies
    # fraîches masquaient les fichiers OI vieux de 48 jours, dans le même
    # répertoire. Chaque famille de fichiers a donc son propre suffixe et son
    # propre plafond. C'est le masquage qui a permis à l'incident de durer.
    Source("bougies 4h backtest (cron 4 h)",
           "backtests/output/pairs_data", 8.0, kind="filedir",
           suffix="_4h_3y.json", only_traded=True,
           note="famille vivante rafraîchie par cron ; les autres familles du "
                "dossier sont héritées et hors périmètre"),
    Source("OI 4h backtest — LU PAR load_oi() ET LA GATE OI LONG",
           "backtests/output/pairs_data", 24.0, kind="filedir",
           suffix="_oi_4h.json", only_traded=True,
           note=("alimente oi_delta_24h_bps, qui ne renvoie PAS None après la "
                 "fin des données : il rend la dernière valeur connue, FIGÉE. "
                 "Un gel ici ne se voit pas — il se déguise en mesure.")),
    Source("funding_history.db (deep history)",
           "backtests/output/funding_history.db", 24.0,
           query="SELECT MAX(ts) FROM funding", unit="ms"),

    # ── source GELÉE : déclarée, donc silencieuse ──
    Source("oi_history.db (archive S3 Hyperliquid)",
           "backtests/output/oi_history.db", 24.0,
           query="SELECT MAX(ts) FROM asset_ctx", unit="s",
           frozen_since="2026-06-29",
           frozen_reason=(
               "l'archive amont `s3://hyperliquid-archive/asset_ctxs/` ne "
               "publie plus après le 2026-06-29 (vérifié le 2026-08-02 : "
               "1137 dates, la dernière est 20260629). Rien à redémarrer de "
               "notre côté ; notre copie est complète jusqu'à la dernière date "
               "publiée. Remplaçant pour la suite : `market_snapshots` de "
               "market.db, horaire, 35 symboles, depuis le 2026-06-10 — avec "
               "19 jours de recouvrement pour valider la jonction."),
           note="RECHERCHE uniquement — le bot live ne lit JAMAIS ce fichier"),
]


def _last_ts(s: Source) -> float | None:
    """Horodatage le plus récent de la source, en secondes epoch."""
    p = os.path.join(ROOT, s.path)
    if s.kind == "filedir":
        if not os.path.isdir(p):
            return None
        names = [f for f in os.listdir(p) if f.endswith(s.suffix)]
        if s.only_traded:
            try:
                from alfred.settings import DEFAULT_PARAMS as _P
                want = {f"{sym}{s.suffix}" for sym in _P.trade_symbols}
                names = [f for f in names if f in want]
            except Exception:
                pass
        mt = [os.path.getmtime(os.path.join(p, f)) for f in names]
        # ⚠ On retient le fichier le PLUS VIEUX, pas le plus récent. Prendre le
        # max masque exactement ce qu'on cherche : un dossier où 33 tokens sont
        # à jour et un seul est figé depuis deux mois passerait « frais ». Une
        # étude ne vaut que par sa plus vieille entrée.
        return min(mt) if mt else None
    if s.kind == "file":
        return os.path.getmtime(p) if os.path.exists(p) else None
    if not os.path.exists(p):
        return None
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        v = con.execute(s.query).fetchone()[0]
        con.close()
    except Exception:
        return None
    if v is None:
        return None
    v = float(v)
    return v / 1000.0 if s.unit == "ms" else v


def check_all(now: float | None = None) -> list[dict]:
    now = now or time.time()
    out = []
    for s in SOURCES:
        last = _last_ts(s)
        age_h = None if last is None else (now - last) / 3600.0
        if last is None:
            status = "MISSING"
            msg = "source introuvable ou vide"
        elif age_h < -1.0 or age_h > 20 * 8766:
            # Un horodatage « dans le futur » ou vieux de plusieurs siècles
            # n'est pas un problème de fraîcheur : c'est une unité mal
            # déclarée (s vs ms) ou un champ mal parsé. Ce module a attrapé
            # exactement ce cas sur lui-même au premier run — et l'âge
            # NÉGATIF passait « OK », puisque −4e8 h reste inférieur au
            # plafond. Une garde de fraîcheur qui valide un horodatage
            # absurde ne garde rien.
            status = "INVALID"
            msg = (f"âge aberrant de {age_h:.1f} h — unité d'horodatage "
                   f"probablement erronée (déclarée « {s.unit} »)")
        elif s.frozen_since:
            status = "FROZEN"
            msg = f"gelée depuis le {s.frozen_since} — {s.frozen_reason}"
        elif s.max_age_h is None:
            status = "OK"
            msg = "aucune attente de fraîcheur"
        elif age_h > s.max_age_h:
            status = "STALE"
            msg = (f"dernier point il y a {age_h:.1f} h, plafond "
                   f"{s.max_age_h:.0f} h — dépassement de "
                   f"{age_h - s.max_age_h:.1f} h")
        else:
            status = "OK"
            msg = f"fraîche ({age_h:.1f} h / {s.max_age_h:.0f} h)"
        out.append({"source": s.name, "path": s.path, "status": status,
                    "age_h": None if age_h is None else round(age_h, 2),
                    "max_age_h": s.max_age_h, "message": msg,
                    "note": s.note,
                    "frozen_since": s.frozen_since or None})
    return out


def critical(rows: list[dict] | None = None) -> list[dict]:
    """Les lignes qui doivent remonter en anomalie — STALE et MISSING."""
    rows = rows if rows is not None else check_all()
    return [r for r in rows if r["status"] in ("STALE", "MISSING", "INVALID")]


def main() -> int:
    rows = check_all()
    ic = {"OK": "✓", "STALE": "🔴", "MISSING": "🔴", "INVALID": "🔴",
          "FROZEN": "❄"}
    print(f"{'':2s} {'source':52s} {'âge':>9s} {'plafond':>8s}  statut")
    for r in rows:
        age = "—" if r["age_h"] is None else f"{r['age_h']:.1f} h"
        cap = "—" if r["max_age_h"] is None else f"{r['max_age_h']:.0f} h"
        print(f"{ic.get(r['status'], '?'):2s} {r['source'][:52]:52s} "
              f"{age:>9s} {cap:>8s}  {r['status']}")
    bad = critical(rows)
    print(f"\n{len(bad)} anomalie(s) de fraîcheur"
          + (" — " + " · ".join(r["source"] for r in bad) if bad else ""))
    for r in rows:
        if r["status"] == "FROZEN":
            print(f"\n❄ {r['source']} — gelée déclarée depuis "
                  f"{r['frozen_since']}\n  {r['message'].split('— ', 1)[-1]}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
