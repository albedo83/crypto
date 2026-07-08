"""Coût des appels IA — tarif Anthropic → $ par appel (2026-07-07).

Source de vérité du prix des modèles utilisés par la couche IA de SENIOR.
Le `usage` Anthropic : input_tokens = input NON caché (plein tarif),
cache_read = 0.1× input, cache_creation = 1.25× input (TTL 5 min), output.

Loggé en event AI_COST (un par appel API), agrégé côté dashboard /master.
Tarif au 2026-07-07 (skill claude-api) — MAJ ici si les prix bougent.
"""
# ($/1M) : (input, output, cache_read, cache_write_5min)
PRICES = {
    "opus-4-8":  (5.00, 25.00, 0.50, 6.25),
    "opus":      (5.00, 25.00, 0.50, 6.25),
    "haiku-4-5": (1.00,  5.00, 0.10, 1.25),
    "haiku":     (1.00,  5.00, 0.10, 1.25),
    "sonnet":    (3.00, 15.00, 0.30, 3.75),
}
_DEFAULT = PRICES["opus-4-8"]        # inconnu → borne haute (opus)


def _rates(model: str):
    m = (model or "").lower()
    for key, r in PRICES.items():
        if key in m:
            return r
    return _DEFAULT


def cost_from_usage(model: str, usage: dict) -> float:
    """$ pour un appel, depuis le dict usage Anthropic."""
    if not usage:
        return 0.0
    r_in, r_out, r_read, r_write = _rates(model)
    return round((
        usage.get("input_tokens", 0) * r_in
        + usage.get("output_tokens", 0) * r_out
        + usage.get("cache_read_input_tokens", 0) * r_read
        + usage.get("cache_creation_input_tokens", 0) * r_write
    ) / 1e6, 6)


def cost_event(source: str, model: str, usage: dict) -> dict:
    """Payload d'un event AI_COST."""
    return {"source": source, "model": model,
            "usage": usage or {}, "cost_usd": cost_from_usage(model, usage)}
