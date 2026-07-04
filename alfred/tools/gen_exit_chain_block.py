#!/usr/bin/env python3
"""Génère le bloc « chaîne de sorties » de docs/architecture.md depuis le CODE
(rules.evaluate_exit = l'ordre réel ; settings.Params = statut actif/retiré).

Une section rédigée à la main diverge toujours (dead_timeout : 2 parjures en
48 h). Une section générée ne peut pas mentir. Le bloc vit entre les balises
<!-- EXIT_CHAIN:BEGIN --> / <!-- EXIT_CHAIN:END --> ; la prose autour reste
libre, les FAITS (ordre, gate, statut) sont machine-vérifiés.

Usage :
    python3 -m alfred.tools.gen_exit_chain_block            # écrit le bloc
    python3 -m alfred.tools.gen_exit_chain_block --check    # pre-commit : diff → exit 1
"""
import inspect
import re
import sys

sys.path.insert(0, "/home/crypto")

from alfred import rules
from alfred.settings import DEFAULT_PARAMS as P

DOC = "/home/crypto/docs/architecture.md"
BEGIN, END = "<!-- EXIT_CHAIN:BEGIN -->", "<!-- EXIT_CHAIN:END -->"

# Kill-switch : prédicat lisant les VALEURS COURANTES de settings.
STATUS = {
    "runner_ext_rule":      lambda: "ACTIVE" if P.runner_ext_strategies else "RETIRÉE",
    "catastrophe_stop_rule": lambda: "ACTIVE",
    "opp_floor_rule":       lambda: "ACTIVE" if P.opp_floor_lock_ratio > 0 else "RETIRÉE",
    "manual_stop_rule":     lambda: "ACTIVE",
    "s9_early_rule":        lambda: "ACTIVE" if P.s9_early_exit_bps > -1e8 else "RETIRÉE",
    "s10_trail_rule":       lambda: "ACTIVE" if P.s10_trailing_trigger < 1e8 else "RETIRÉE",
    "s8_dead_rule":         lambda: "ACTIVE" if P.s8_dead_mfe_max_bps > -1e4 else "RETIRÉE",
    "s8_inlife_rule":       lambda: "ACTIVE" if P.s8_inlife_params else "RETIRÉE",
    "prop_trail_rule":      lambda: "ACTIVE" if P.prop_trail_params else "RETIRÉE",
    "traj_cut_rule":        lambda: "ACTIVE" if P.traj_cut_strategies else "RETIRÉE",
    "s9_early_dead_rule":   lambda: "ACTIVE" if P.s9_early_dead_mfe_max_bps > -1e4 else "RETIRÉE",
    "btc_drop_cut_rule":    lambda: "ACTIVE" if P.btc_drop_cut_ret_4h_bps > -1e8 else "RETIRÉE",
    "dead_timeout_rule":    lambda: "ACTIVE" if P.dead_timeout_mfe_cap_bps > -1e4 else "RETIRÉE",
}


def extract_chain() -> list[tuple[str, str]]:
    """[(règle, gate)] dans l'ordre RÉEL d'evaluate_exit. gate ∈ tick|4h-close."""
    src = inspect.getsource(rules.evaluate_exit)
    out = []
    in_trail = False
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("if trail_gate:"):
            in_trail = True
            continue
        # sortie du bloc trail_gate : ligne de code au même niveau que le if
        if in_trail and line and not line.startswith(" " * 8) and s:
            in_trail = False
        if in_trail and not s.startswith(("d =", "if d", "return d")):
            pass
        m = re.search(r"(\w+_rule)\(", s)
        if m and m.group(1) in STATUS:
            gate = "4h-close" if in_trail else "tick 20s"
            if (m.group(1), gate) not in out:
                out.append((m.group(1), gate))
        if "\"timeout\"" in s and "ExitDecision" in s:
            out.append(("timeout", "tick 20s"))
    return out


def build_block() -> str:
    lines = [BEGIN,
             "<!-- Généré par alfred/tools/gen_exit_chain_block.py — NE PAS ÉDITER À LA MAIN.",
             "     Pre-commit : --check refuse tout commit si ce bloc diverge du code. -->",
             "",
             "| # | Règle | Évaluation | Statut (settings) |",
             "|---|-------|------------|-------------------|"]
    for i, (rule, gate) in enumerate(extract_chain(), 1):
        status = STATUS[rule]() if rule in STATUS else "ACTIVE"
        name = rule.replace("_rule", "")
        lines.append(f"| {i} | `{name}` | {gate} | {status} |")
    lines.append(END)
    return "\n".join(lines)


def main() -> int:
    check = "--check" in sys.argv
    block = build_block()
    doc = open(DOC).read()
    if BEGIN not in doc:
        if check:
            print("gen_exit_chain_block: balises absentes du doc — bloc jamais installé",
                  file=sys.stderr)
            return 1
        # première installation : insérer après le titre §8
        anchor = "## 8. Chaîne de sorties"
        idx = doc.index(anchor)
        eol = doc.index("\n", idx) + 1
        doc = doc[:eol] + "\n" + block + "\n" + doc[eol:]
        open(DOC, "w").write(doc)
        print("bloc installé dans architecture.md §8")
        return 0
    cur = doc[doc.index(BEGIN):doc.index(END) + len(END)]
    if cur == block:
        print("exit-chain doc ✓ (généré = code)")
        return 0
    if check:
        print("❌ architecture.md §8 (bloc EXIT_CHAIN) diverge du code — lancer "
              "python3 -m alfred.tools.gen_exit_chain_block puis re-commiter",
              file=sys.stderr)
        return 1
    doc = doc.replace(cur, block)
    open(DOC, "w").write(doc)
    print("bloc régénéré")
    return 0


if __name__ == "__main__":
    sys.exit(main())
