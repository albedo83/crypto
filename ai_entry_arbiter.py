"""Arbitre d'entrée IA — l'IA décide au moment de la prise de position (SENIOR).

Cœur importable, appelé SYNCHRONEMENT par le bot live (botinstance) une fois par
scan 4h sur le LOT complet des candidats : l'IA renvoie, par symbole, un verdict
{decision: GO|VETO, factor, confidence, reason, risk_flags}. Le bot applique le
veto (annule l'entrée) ou le facteur (réduit la taille).

Discipline : un gate LLM est inbacktestable → l'overlay vit ICI et dans
botinstance UNIQUEMENT (jamais dans rules.py / le backtest). La valeur est
mesurée en live par ai_arbiter_scorecard.py (contrefactuel règles-seules).

Sécurité : `arbitrate_safe()` borne (factor ∈ [factor_min, 1.0]), applique un
timeout strict et **fail-open** (toute erreur/timeout ⇒ dict vide ⇒ le bot trade
selon les règles, aucun veto). Le SDK Anthropic est importé paresseusement.

Usage CLI (test, n'agit sur rien) :
    ./ai_entry_arbiter.py --dry-run   # assemble le prompt depuis l'état SENIOR
    ./ai_entry_arbiter.py --no-act    # vrai appel Opus, décisions en stdout
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
DEFAULT_FACTOR_MIN = 0.5

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-arbiter")

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
# Disjoncteur : drapeau écrit par ai_arbiter_scorecard.py, lu par le bot. Sa
# présence dégrade l'arbitre en 'shadow' (n'agit plus). Réarmement = suppression.
TRIP_FILE = os.path.join(REPO_ROOT, "alfred", "data", "bots", "live",
                         "arbiter_tripped.json")


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
    """Lit la config arbitre depuis l'environnement (.env chargé par le bot).

    mode : 'act' (agit) | 'shadow' (décide+logge sans agir) | 'off'.
    enabled=0 ⇒ kill-switch total. cb_* = disjoncteur (géré par botinstance)."""
    env = os.environ.get
    enabled = env("AI_ARBITER_ENABLED", "0") == "1"   # OFF par défaut (opt-in)
    mode = env("AI_ARBITER_MODE", "shadow").strip().lower()
    if not enabled:
        mode = "off"
    return {
        "enabled": enabled,
        "mode": mode,                                  # off|shadow|act
        "model": env("AI_ARBITER_MODEL", DEFAULT_MODEL),
        "timeout": float(env("AI_ARBITER_TIMEOUT", str(DEFAULT_TIMEOUT))),
        "factor_min": float(env("AI_ARBITER_FACTOR_MIN", str(DEFAULT_FACTOR_MIN))),
        "veto_conf_min": float(env("AI_ARBITER_VETO_CONF_MIN", "0.0")),
        # v1.15.0 — doctrine <50 trades : le hard-veto (suppression totale du
        # trade, seul pouvoir DESTRUCTIF de la couche IA) est dégradé en
        # haircut factor_min tant qu'il n'est pas re-autorisé explicitement.
        # Bonus : le trade pris à taille réduite fournit son propre
        # contrefactuel au scorecard (un veto supprimé = pending à vie).
        # Ré-armer quand n_resolved ≥ 50 et Δ>0 : AI_ARBITER_VETO_ACT=1.
        "veto_act": env("AI_ARBITER_VETO_ACT", "0") == "1",
        "cb_min": int(env("AI_ARBITER_CB_MIN", "20")),
        "cb_loss": float(env("AI_ARBITER_CB_LOSS", "-40")),
        # Hystérésis inter-scan : réinjecte la décision précédente sur un même
        # symbole si < prior_ttl_h heures (anti flip-flop). 0 = désactivé.
        "prior_ttl_h": float(env("AI_ARBITER_PRIOR_TTL_H", "12")),
    }

SYSTEM_PROMPT = """\
Tu es l'arbitre d'entrée d'un bot de trading LIVE (SENIOR) sur Hyperliquid
(altcoins perp, levier 2×, holds ~24-48h). À CHAQUE scan 4h, le moteur de règles
(walk-forward validé) propose un lot d'entrées. Tu ARBITRES chaque entrée :
tu peux l'ANNULER (veto) ou RÉDUIRE sa taille. Tu n'augmentes jamais la taille.

TON RÔLE — apporter ce que les formules NE voient PAS :
- **Choc BTC récent** : `btc_ret_4h_bps` = mouvement BTC sur la bougie 4h en cours
  (le régime btc_z est lent/30j et ne le capte pas). Une chute BTC marquée cette
  bougie (ex. < −150 bps) est une raison forte de VETO/réduire un LONG alt frais
  (les alts amplifient les chutes BTC) ; un pump BTC marqué pénalise un SHORT frais.
- **BREADTH marché (capitulation)** : `capitulation` décrit TOUT le marché HL —
  `down20_pct`/`down10_pct` = % d'alts en ≤−20%/≤−10% sur 24h, `median_24h_bps` = le
  tape global. Une capitulation large (down20_pct élevé, médiane franchement rouge)
  rend un **LONG alt frais TRÈS risqué** → raison FORTE de VETO/haircut, même si le
  token paraît fort : en liquidation sectorielle les positions à levier sautent en
  premier. Symétrique — un pump large pénalise un SHORT frais. C'est un signal de
  contexte à pondérer, pas un seuil automatique.
- **PORTEFEUILLE (concentration)** : `portfolio.open_positions` = le book DÉTENU
  (signal, direction, secteur, ur, âge) + `effective_n` (nombre effectif de paris
  indépendants — bas = book concentré). Les gates du moteur COMPTENT les positions ;
  toi tu raisonnes la CORRÉLATION : un énième candidat même-direction/même-secteur
  quand le book penche déjà de ce côté n'ajoute pas d'alpha, il ajoute du beta —
  raison légitime de haircut (voire veto si le book est déjà sous l'eau du même
  côté). Un candidat qui DIVERSIFIE (direction/secteur opposés au book) mérite au
  contraire son GO plein. Juge le lot ENSEMBLE : les candidats de ce scan entrent
  tous au même close.
- Danger concret hors-modèle : depeg, incident/hack exchange, unlock/déblocage de
  tokens imminent, délisting, exploit, gouvernance/news majeure sur le token.
- Incohérence flagrante setup vs contexte : fade (S9) ou mean-reversion (S5) à
  contre-courant d'une tendance directionnelle forte et alignée ; LONG en bear
  marqué / SHORT en bull marqué sur une strat régime-sensible ; structure
  (funding/OI/dispersion) qui signale une poursuite plutôt qu'un retour.
- **SHORT qui combat un momentum HAUSSIER aligné (RÈGLE FERME)** : si une entrée
  SHORT (S5/S9/S10 fades) arrive alors que le token monte nettement (`ret_24h_bps`
  positif et fort, ou breakout `bo=UP` net dans signal_info) ET que BTC monte sur la
  bougie (`btc_ret_4h_bps` > +100) → **VETO par défaut**, sauf preuve CLAIRE
  d'essoufflement (ex. OI en forte baisse, divergence marquée, exhaustion). Shorter
  une force alignée token+BTC est le cas qui perd le plus. Symétrique pour un LONG
  qui combat une chute alignée token+BTC.
- **S5 LONG sans up-streak confirmé (RÈGLE MESURÉE, haircut)** : le signal S5 est
  une divergence sectorielle. Quand le token diverge MAIS n'est pas lui-même en
  tendance haussière propre (`consec_up` < 2, càd 0-1 bougie consécutive en
  hausse), la divergence est souvent un FAUX breakout qui se retourne — cause
  mesurée : `consec_up`<2 → 31 % de catastrophes (vs 13 % en up-streak ≥2), WR 55 %
  vs 81 %. **HAIRCUT par défaut** (facteur ~0.5-0.7) sur ces S5 LONG — **PAS un
  veto** : ces trades gagnent encore 55 % du temps, les retirer tuerait l'edge (le
  gate dur détruit −$44k en backtest) ; l'objectif est de RÉDUIRE l'exposition au
  retournement, pas de le supprimer. GO plein si `consec_up` ≥ 2 (up-streak confirmé
  = vrai momentum qui continue). Ne s'applique QU'aux S5 LONG.
- Setup mécaniquement marginal alors que le floor de frais HL ~9 bps RT rend un
  edge faible fragile.

DISCIPLINE :
- Le moteur a un EDGE PROUVÉ en agrégat. Ton DÉFAUT est GO pleine taille. Ne mets
  VETO / facteur < 1 que si tu as une raison CONCRÈTE, ancrée sur le contexte
  fourni. Ne re-litige pas la stratégie elle-même (elle est validée) ; tu juges CE
  setup, MAINTENANT, avec l'info que les chiffres n'ont pas.
- Tu vois le LOT complet : tiens compte de la corrélation / concentration (éviter
  d'empiler des entrées redondantes dans le même sens/secteur si le risque est
  concentré).
- Pas d'hallucination de chiffres : uniquement les valeurs du contexte fourni.

COHÉRENCE INTER-SCAN (anti flip-flop) :
- Si un candidat porte `prior_decision`, c'est TA décision sur CE même setup au
  scan précédent (il y a `hours_ago` h). Traite-la comme un prior fort. Si tu
  l'as VÉTÉ, ne reviens en GO que si le contexte a **matériellement** changé
  (raison concrète, pas une simple oscillation BTC de bruit) : un knife /
  regime_mismatch qui persiste **reste un VETO**. Inversement, ne t'entête pas
  sur un veto si l'état a clairement basculé. Justifie tout revirement dans
  `reason`. Le même setup ré-évalué à l'identique ne doit pas changer d'avis.

SORTIE — réponds EXCLUSIVEMENT en JSON valide, un objet dont les clés sont les
symboles du lot, rien avant/après :
{
  "<SYMBOL>": {
    "decision": "GO" | "VETO",
    "factor": <float 0.5-1.0>,   // taille relative si GO ; ignoré si VETO
    "confidence": <float 0.0-1.0>,
    "reason": "<=160 chars FR, factuel",
    "risk_flags": ["<tag court>", ...]   // 0-4 ex: depeg, unlock, knife, regime_mismatch, crowding, thin_edge, concentration
  },
  ...
}
Une entrée par symbole du lot. Termes techniques OK (bps, btc_z, MFE, div).
"""

# Traçabilité (supervision v2 ph.1) : hash du prompt système — le
# scorecard sépare les populations quand le prompt change (champion/
# challenger phase 2). À logger dans chaque event de décision.
import hashlib as _hl
# v1.15.0 : DOCTRINE_DIGEST inclus — un changement de doctrine changeait
# la population de décisions sous un hash constant (inauditables).
PROMPT_HASH = _hl.sha256((SYSTEM_PROMPT + DOCTRINE_DIGEST).encode()).hexdigest()[:10]



def build_user_prompt(candidates: list[dict], market: dict) -> str:
    return (
        "Lot d'entrées proposées par les règles à ce scan 4h. Arbitre CHAQUE "
        "symbole.\n\nRégime / marché :\n```json\n"
        + json.dumps(market, indent=2, default=str, ensure_ascii=False)
        + "\n```\n\nCandidats (déjà triés par priorité z) :\n```json\n"
        + json.dumps(candidates, indent=2, default=str, ensure_ascii=False)
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


def _normalize(v: dict, factor_min: float) -> dict:
    """Borne et nettoie un verdict par symbole."""
    dec = str(v.get("decision", "GO")).upper()
    dec = "VETO" if dec == "VETO" else "GO"
    try:
        factor = float(v.get("factor", 1.0))
    except (TypeError, ValueError):
        factor = 1.0
    factor = max(factor_min, min(1.0, factor))
    try:
        conf = round(max(0.0, min(1.0, float(v.get("confidence", 0.0)))), 3)  # clamp [0,1]
    except (TypeError, ValueError):
        conf = 0.0
    flags = v.get("risk_flags")
    return {
        "decision": dec,
        "factor": round(factor, 3),
        "confidence": conf,
        "reason": str(v.get("reason", ""))[:200],
        "risk_flags": flags if isinstance(flags, list) else [],
    }


def arbitrate(candidates: list[dict], market: dict, *,
              model: str = DEFAULT_MODEL,
              factor_min: float = DEFAULT_FACTOR_MIN) -> dict:
    """Appel direct (peut lever / bloquer). Retourne
    {"verdicts": {sym: {...}}, "meta": {...}}. Préférer arbitrate_safe()."""
    out = _call_opus(SYSTEM_PROMPT, build_user_prompt(candidates, market), model)
    syms = {c["symbol"] for c in candidates}
    norm = {s: _normalize(v, factor_min)
            for s, v in (out["verdicts"] or {}).items() if s in syms}
    return {"verdicts": norm, "meta": out["meta"]}


def arbitrate_safe(candidates: list[dict], market: dict, *,
                   model: str = DEFAULT_MODEL,
                   timeout: float = DEFAULT_TIMEOUT,
                   factor_min: float = DEFAULT_FACTOR_MIN) -> dict:
    """Wrapper borné + timeout + FAIL-OPEN.

    Retour : {"verdicts": {sym: verdict}, "meta": {...}} si succès,
    sinon {"verdicts": {}, "meta": {"failopen": <raison>}} → le bot trade
    selon les règles (aucun veto, factor=1)."""
    if not candidates:
        return {"verdicts": {}, "meta": {"empty": True}}
    fut = _EXECUTOR.submit(arbitrate, candidates, market,
                           model=model, factor_min=factor_min)
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
                        help="Assemble le prompt depuis l'état SENIOR, pas d'appel")
    parser.add_argument("--no-act", action="store_true",
                        help="Vrai appel Opus, décisions en stdout (n'agit pas)")
    parser.add_argument("--model", default=os.environ.get("AI_ARBITER_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()

    # Construit un lot représentatif depuis /api/signals + /api/state de SENIOR.
    from supervisor import load_env, BotClient
    load_env()
    c = BotClient("live", os.environ.get("DASHBOARD_USER", ""),
                  os.environ.get("DASHBOARD_PASS", ""))
    st = c.fetch("/api/state") or {}
    sigs = c.fetch("/api/signals") or []
    market = {}
    if isinstance(st, dict):
        market = {k: (st.get("market") or {}).get(k) for k in
                  ("btc_30d", "btc_7d", "disp_24h", "disp_7d", "n_stress_global")}
        market["btc_z"] = st.get("btc_z_30d") or st.get("btc_z")
    # /api/signals : {"signals": {SYM: {..., "triggered": [...], "proximity": {...}}}}
    smap = (sigs or {}).get("signals", {}) if isinstance(sigs, dict) else {}

    def _ctx(sym, d):
        return {"symbol": sym, "strategy": None, "dir": None,
                "sector": d.get("sector"), "sector_div": d.get("sector_div"),
                "ret_7d_bps": d.get("ret_7d_bps"), "vol_ratio": d.get("vol_ratio"),
                "oi_delta_1h": d.get("oi_delta_1h"), "funding_bps": d.get("funding_bps"),
                "crowding": d.get("crowding")}

    cand = []
    for sym, d in smap.items():
        trig = d.get("triggered") or []
        if trig:
            for t in trig:
                e = _ctx(sym, d)
                e["strategy"] = t.get("strategy") if isinstance(t, dict) else t
                e["dir"] = t.get("direction") if isinstance(t, dict) else None
                cand.append(e)
    if not cand:
        # Aucun signal déclenché à cet instant (hors close 4h) → échantillon par
        # proximité pour exercer le prompt/appel (test seulement).
        ranked = sorted(smap.items(),
                        key=lambda kv: max((kv[1].get("proximity") or {}).values() or [0]),
                        reverse=True)[:3]
        for sym, d in ranked:
            e = _ctx(sym, d)
            prox = d.get("proximity") or {}
            e["strategy"] = max(prox, key=prox.get) if prox else "?"
            e["_note"] = "ÉCHANTILLON proximité (pas un vrai trigger)"
            cand.append(e)
        print("[arbiter] aucun trigger actif → échantillon proximité pour test")
    if not cand:
        print("[arbiter] aucun candidat — rien à arbitrer.")
        return 0

    print(f"[arbiter] {len(cand)} candidat(s) | modèle {args.model}")
    if args.dry_run:
        print(build_user_prompt(cand, market)[:2500])
        print("\n[arbiter] --dry-run: arrêt avant Opus")
        return 0
    res = arbitrate_safe(cand, market, model=args.model)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
