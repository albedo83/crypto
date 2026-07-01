"""Arbitre de SORTIE IA — l'IA agit sur les positions ouvertes (SENIOR).

Cœur importable, appelé par le bot live (botinstance) sur un THROTTLE (≤1×/h) sur
le lot des positions en « zone candidate ». L'IA renvoie, par symbole, un verdict
{action: HOLD|LOCK|CUT, stop_usdt, confidence, reason, risk_flags} :
  - CUT  : couper un PERDANT dont la trajectoire est catastrophique (doomed).
  - LOCK : verrouiller un GAGNANT en posant/relevant un stop protecteur (cliquet).
  - HOLD : ne rien faire (défaut — les règles gèrent la majorité des sorties).

Déploiement asymétrique (choix utilisateur) :
  - LOCK est non-destructif → AGIT dès que l'arbitre est enabled.
  - CUT est destructif → SHADOW d'abord (`AI_EXIT_CUT_MODE=shadow`), bascule `act`
    seulement sur preuve du scorecard.
  - L'IA ne ferme JAMAIS un gagnant (LOCK = stop protecteur uniquement).

Discipline : gate LLM inbacktestable → overlay live-only (jamais dans rules.py /
le backtest). Valeur mesurée par ai_exit_scorecard.py (contrefactuel règles).
Sécurité : `arbitrate_safe()` borne, timeout strict, **fail-safe** (toute
erreur/timeout ⇒ dict vide ⇒ aucune action). SDK Anthropic lazy-import.

Usage CLI (test, n'agit sur rien) :
    ./ai_exit_arbiter.py --dry-run   # assemble le prompt depuis les positions SENIOR
    ./ai_exit_arbiter.py --no-act    # vrai appel Opus, décisions en stdout
"""

from __future__ import annotations

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

from ai_doctrine import DOCTRINE_DIGEST

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_TIMEOUT = 12.0

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-exit")

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
# Disjoncteur SÉPARÉ de l'arbitre d'entrée : drapeau écrit par ai_exit_scorecard.py.
# Sa présence dégrade l'arbitre de sortie en observation (CUT et LOCK n'agissent plus).
TRIP_FILE = os.path.join(REPO_ROOT, "alfred", "data", "bots", "live",
                         "exit_arbiter_tripped.json")


def is_tripped() -> bool:
    return os.path.exists(TRIP_FILE)


def trip(reason: str, detail: dict | None = None) -> None:
    try:
        with open(TRIP_FILE, "w") as f:
            json.dump({"reason": reason, "detail": detail or {}}, f)
    except Exception:
        pass


def rearm() -> None:
    try:
        os.remove(TRIP_FILE)
    except FileNotFoundError:
        pass


def config() -> dict:
    """Config arbitre de sortie depuis l'environnement (.env chargé par le bot).

    enabled=0 ⇒ kill-switch total. cut_mode gate UNIQUEMENT le CUT (LOCK agit dès
    enabled, choix hybride). conf_min = confiance mini pour agir. throttle_s = un
    appel LLM au plus toutes les N s. cut_ur_max / lock_ur_min = zone candidate."""
    env = os.environ.get
    enabled = env("AI_EXIT_ENABLED", "0") == "1"          # OFF par défaut (opt-in)
    cut_mode = env("AI_EXIT_CUT_MODE", "shadow").strip().lower()
    if cut_mode not in ("shadow", "act"):
        cut_mode = "shadow"
    return {
        "enabled": enabled,
        "cut_mode": cut_mode,                              # shadow|act (CUT seulement)
        "model": env("AI_EXIT_MODEL", DEFAULT_MODEL),
        "timeout": float(env("AI_EXIT_TIMEOUT", str(DEFAULT_TIMEOUT))),
        "conf_min": float(env("AI_EXIT_CONF_MIN", "0.6")),
        "throttle_s": float(env("AI_EXIT_THROTTLE_S", "3600")),
        # Zone candidate : ne soumet à l'IA que les positions où son jugement compte.
        "cut_ur_max_bps": float(env("AI_EXIT_CUT_UR_MAX_BPS", "-300")),  # perdant profond
        "lock_ur_min_bps": float(env("AI_EXIT_LOCK_UR_MIN_BPS", "300")),  # gagnant à protéger
        "cb_min": int(env("AI_EXIT_CB_MIN", "20")),
        "cb_loss": float(env("AI_EXIT_CB_LOSS", "-40")),
        "prior_ttl_h": float(env("AI_EXIT_PRIOR_TTL_H", "12")),
    }


SYSTEM_PROMPT = """\
Tu es l'arbitre de SORTIE d'un bot de trading LIVE (SENIOR) sur Hyperliquid
(altcoins perp, levier 2×, holds ~24-48h). Le moteur de règles (walk-forward
validé) gère DÉJÀ la plupart des sorties (catastrophe-stop, timeout, prop_trail,
traj_cut, dead_timeout, opp_floor, manual_stop…). On te présente les positions
ouvertes en ZONE CANDIDATE. Pour CHACUNE, tu décides UNE action :

- **HOLD** (défaut) : ne rien faire, laisser les règles gérer. C'est le cas le
  plus fréquent — n'agis QUE sur conviction concrète.
- **CUT** : couper MAINTENANT un PERDANT dont la trajectoire est catastrophique
  (doomed) — pente descendante persistante depuis le MFE, collé au MAE, régime
  aligné contre la position (btc_z / btc_ret_4h_bps), aucun signe de rebond. Tu ne
  CUT JAMAIS un gagnant ni une position simplement « rouge mais respirante » (le
  chop est l'ami d'une stratégie de retour-à-la-moyenne — la plupart des perdants
  modérés rebondissent). CUT seulement le couteau qui tombe sans fond.
- **LOCK** : sur un GAGNANT (`unrealized_bps` > 0) dont le gain mérite d'être
  protégé (MFE élevé en train d'être rendu, ou risque/récompense devenu défavorable
  face au régime), pose un plancher protecteur via `stop_usdt` = plancher en $ sur
  le PnL NET. Il DOIT être strictement < le PnL net actuel (`pnl_usdt`) et laisser
  de la marge sous le prix (pas de déclenchement immédiat). Tu ne fermes PAS le
  gagnant — tu le protèges et le laisses courir. Si un `manual_stop_usdt` existe
  déjà, ne propose un LOCK que pour le RELEVER (plancher plus haut).

RÈGLES FERMES :
- Le moteur a un EDGE PROUVÉ ; ton défaut est HOLD. N'agis (CUT/LOCK) que sur une
  raison ancrée dans le contexte fourni.
- Jamais de CUT sur un gagnant. Jamais de LOCK qui se déclencherait immédiatement.
- Pas d'hallucination de chiffres : uniquement les valeurs du contexte fourni.
- Mean-reversion : un perdant modéré qui respire n'est PAS un CUT. Le CUT vise la
  trajectoire désespérée sans rebond, pas le rouge ordinaire.

COHÉRENCE INTER-SCAN : si une position porte `prior_decision` (ta décision
précédente, il y a `hours_ago` h), traite-la comme un prior fort — ne change d'avis
que si l'état a matériellement bougé, justifie dans `reason`.

SORTIE — réponds EXCLUSIVEMENT en JSON valide, un objet dont les clés sont les
symboles, rien avant/après :
{
  "<SYMBOL>": {
    "action": "HOLD" | "LOCK" | "CUT",
    "stop_usdt": <float|null>,        // plancher $ si LOCK ; null sinon
    "confidence": <float 0.0-1.0>,
    "reason": "<=160 chars FR, factuel",
    "risk_flags": ["<tag court>", ...]  // 0-4 ex: knife, doomed, giveback, regime_mismatch, exhaustion
  },
  ...
}
Une entrée par symbole. Termes techniques OK (bps, btc_z, MFE, MAE, div).
"""


def build_user_prompt(positions: list[dict], market: dict) -> str:
    return (
        "Positions ouvertes en zone candidate (SENIOR). Décide UNE action par "
        "symbole.\n\nRégime / marché :\n```json\n"
        + json.dumps(market, indent=2, default=str, ensure_ascii=False)
        + "\n```\n\nPositions :\n```json\n"
        + json.dumps(positions, indent=2, default=str, ensure_ascii=False)
        + "\n```\n\nRends ton objet JSON (une clé par symbole)."
    )


def _call_opus(system: str, user: str, model: str) -> dict:
    """Appel brut Anthropic, parse l'objet JSON. Lève sur erreur."""
    import anthropic

    client = anthropic.Anthropic()
    sysblocks = [
        {"type": "text", "text": system},
        {"type": "text",
         "text": "# Référence stratégies & sorties du bot\n\n" + DOCTRINE_DIGEST,
         "cache_control": {"type": "ephemeral"}},
    ]
    resp = client.messages.create(
        model=model, max_tokens=1500, system=sysblocks,
        messages=[{"role": "user", "content": user}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    raw = "".join(parts).strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise RuntimeError(f"Pas de JSON:\n{raw[:500]}")
    data = json.loads(match.group(0))
    usage = getattr(resp, "usage", None)
    meta = {"_model": model}
    if usage:
        meta["_usage"] = {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
        }
    return {"verdicts": data, "meta": meta}


def _normalize(v: dict) -> dict:
    """Borne et nettoie un verdict par symbole."""
    act = str(v.get("action", "HOLD")).upper()
    if act not in ("HOLD", "LOCK", "CUT"):
        act = "HOLD"
    try:
        stop = v.get("stop_usdt")
        stop = float(stop) if stop is not None else None
    except (TypeError, ValueError):
        stop = None
    try:
        conf = round(max(0.0, min(1.0, float(v.get("confidence", 0.0)))), 3)  # L2 : clamp [0,1]
    except (TypeError, ValueError):
        conf = 0.0
    flags = v.get("risk_flags")
    return {
        "action": act,
        "stop_usdt": stop,
        "confidence": conf,
        "reason": str(v.get("reason", ""))[:200],
        "risk_flags": flags if isinstance(flags, list) else [],
    }


def arbitrate(positions: list[dict], market: dict, *,
              model: str = DEFAULT_MODEL) -> dict:
    """Appel direct (peut lever / bloquer). Retourne
    {"verdicts": {sym: {...}}, "meta": {...}}. Préférer arbitrate_safe()."""
    out = _call_opus(SYSTEM_PROMPT, build_user_prompt(positions, market), model)
    syms = {p["symbol"] for p in positions}
    norm = {s: _normalize(v)
            for s, v in (out["verdicts"] or {}).items() if s in syms}
    return {"verdicts": norm, "meta": out["meta"]}


def arbitrate_safe(positions: list[dict], market: dict, *,
                   model: str = DEFAULT_MODEL,
                   timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Wrapper borné + timeout + FAIL-SAFE.

    Retour : {"verdicts": {sym: verdict}, "meta": {...}} si succès, sinon
    {"verdicts": {}, "meta": {"failopen": <raison>}} → AUCUNE action (les règles
    continuent de gérer les sorties)."""
    if not positions:
        return {"verdicts": {}, "meta": {"empty": True}}
    fut = _EXECUTOR.submit(arbitrate, positions, market, model=model)
    try:
        return fut.result(timeout=timeout)
    except FTimeout:
        fut.cancel()
        return {"verdicts": {}, "meta": {"failopen": f"timeout>{timeout}s"}}
    except Exception as e:
        return {"verdicts": {}, "meta": {"failopen": f"{type(e).__name__}:{str(e)[:80]}"}}


# ── CLI (test seulement — n'agit sur rien) ──────────────────────────────


def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Assemble le prompt depuis les positions SENIOR, pas d'appel")
    parser.add_argument("--no-act", action="store_true",
                        help="Vrai appel Opus, décisions en stdout (n'agit pas)")
    parser.add_argument("--model", default=os.environ.get("AI_EXIT_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()

    from supervisor import load_env, BotClient
    load_env()
    c = BotClient("live", os.environ.get("DASHBOARD_USER", ""),
                  os.environ.get("DASHBOARD_PASS", ""))
    st = c.fetch("/api/state") or {}
    market = {}
    if isinstance(st, dict):
        market = {k: (st.get("market") or {}).get(k) for k in
                  ("btc_30d", "btc_7d", "disp_24h", "disp_7d", "n_stress_global")}
        market["btc_z"] = st.get("btc_z_30d") or st.get("btc_z")
        market["btc_ret_4h_bps"] = (st.get("market") or {}).get("btc_ret_4h_bps")
    positions = st.get("positions") if isinstance(st, dict) else None
    keep = ("symbol", "strategy", "direction", "unrealized_bps", "pnl_usdt",
            "size_usdt", "hold_hours", "remaining_hours", "mae_bps", "mfe_bps",
            "mfe_at_h", "manual_stop_usdt", "opp_floor_bps", "signal_info")
    pos = []
    for p in (positions or []):
        if not isinstance(p, dict):
            continue
        pos.append({k: p.get(k) for k in keep if k in p})
    if not pos:
        print("[exit-arbiter] aucune position ouverte — rien à arbitrer.")
        return 0

    print(f"[exit-arbiter] {len(pos)} position(s) | modèle {args.model}")
    if args.dry_run:
        print(build_user_prompt(pos, market)[:2500])
        print("\n[exit-arbiter] --dry-run: arrêt avant Opus")
        return 0
    res = arbitrate_safe(pos, market, model=args.model)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
