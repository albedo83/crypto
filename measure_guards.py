"""Garde-fous de mesure — une chaîne valide ses entrées ou REFUSE d'émettre.

Motivation (2026-07-31) : cinq mesures fausses en une semaine, **toutes
silencieuses**. Aucune n'a planté, toutes ont émis un chiffre :

  1. divergence sectorielle sur une carte périmée → 8 tokens invisibles
  2. univers recopié à la main → 28 simulés contre 35 tradés (v12.9.7)
  3. référence annoncée « S5 à 3.0 » qui tournait à 1.0
  4. `docs/backtests.md` régénéré sous config dormante
  5. percentiles de rendement calculés sur une colonne de zéros
     (`net_bps` absent du SELECT)

Le point commun n'est pas la nature du bug, c'est le **mode de défaillance** :
un nombre plausible sort au lieu d'une erreur. `backtests/fingerprint.py` traite
le versant *conditions* (sous quoi la mesure a tourné) ; ce module traite le
versant *entrées* (la mesure a-t-elle de quoi être calculée).

Principe : **pas de nombre plutôt qu'un mauvais nombre.** Une colonne absente,
une série constante, un effectif sous le plancher → exception, pas de rapport.

Stdlib uniquement : importable par le harnais de backtest comme par les
sentinelles (`analysis/strategy_review.py` n'a pas de dépendances lourdes).

Usage :
    from measure_guards import require_series, require_column, MeasureError
    require_series("net_bps live", nets, min_n=10)
"""

from __future__ import annotations


class MeasureError(RuntimeError):
    """La mesure n'a pas de quoi être calculée — ne rien émettre."""


def require_column(rows, key: str, *, label: str = "") -> list:
    """La colonne existe, et n'est pas intégralement absente/nulle.

    C'est exactement l'incident n°5 : `net_bps` manquait du SELECT, `.get()`
    renvoyait None, la moyenne tombait à 0.0 et les percentiles sortaient
    quand même — sur une population de zéros.
    """
    what = label or key
    if not rows:
        raise MeasureError(f"{what}: aucune ligne")
    if not any(key in r for r in rows):
        raise MeasureError(
            f"{what}: colonne '{key}' ABSENTE de toutes les lignes "
            f"(champs vus : {sorted(set().union(*(set(r) for r in rows[:5])))})")
    vals = [r.get(key) for r in rows]
    if all(v is None for v in vals):
        raise MeasureError(f"{what}: colonne '{key}' entièrement nulle")
    return vals


def require_series(label: str, values, *, min_n: int = 1,
                   allow_constant: bool = False) -> list[float]:
    """Série exploitable : assez de points, pas de None, variance non nulle."""
    if values is None:
        raise MeasureError(f"{label}: série absente")
    vals = list(values)
    if len(vals) < min_n:
        raise MeasureError(f"{label}: n={len(vals)} < plancher {min_n}")
    if any(v is None for v in vals):
        n_none = sum(1 for v in vals if v is None)
        raise MeasureError(f"{label}: {n_none}/{len(vals)} valeurs nulles")
    try:
        vals = [float(v) for v in vals]
    except (TypeError, ValueError) as e:
        raise MeasureError(f"{label}: valeurs non numériques ({e})") from e
    if not allow_constant and len(set(vals)) == 1:
        raise MeasureError(
            f"{label}: série CONSTANTE à {vals[0]!r} sur {len(vals)} points — "
            f"symptôme classique d'une colonne manquante ou d'un défaut par "
            f"défaut, pas d'une vraie mesure")
    return vals


def require_positive(label: str, value, *, strict: bool = True) -> float:
    if value is None:
        raise MeasureError(f"{label}: absent")
    v = float(value)
    if (v <= 0) if strict else (v < 0):
        raise MeasureError(f"{label}: {v!r} n'est pas "
                           f"{'strictement ' if strict else ''}positif")
    return v


def require_match(label: str, a, b, *, what_a: str = "a", what_b: str = "b"):
    """Deux sources censées coïncider — sinon on ne mesure pas, on alerte."""
    if a != b:
        raise MeasureError(f"{label}: {what_a}={a!r} ≠ {what_b}={b!r}")
    return a
