"""Test : la décision d'attente du scan d'entrée 4h est indépendante de l'ordre
sorties/scan au sein d'un tick (incident live/mirror du 2026-10-02 16:03).

Simule le scheduler (ticks de 20 s) autour d'une borne 4h, avec des positions
dont le timeout tombe dans la fenêtre du scan, et force les DEUX ordonnancements :
  exits_first : timeouts fermés, puis tentative de scan
  scan_first  : tentative de scan, puis timeouts fermés (l'ordre qui a causé l'incident)
Invariants vérifiés avec `alfred.botinstance.entry_scan_waits` :
  1. au moment où le scan passe, aucune position n'est échue mais encore tenue ;
  2. les deux ordres libèrent exactement les mêmes slots au moment du scan ;
  3. l'ANCIENNE règle (cooldowns seuls) viole 1 en scan_first → le test détecte
     une régression ;
  4. une prolongation runner_ext ne bloque pas ; une fermeture qui échoue ne
     bloque pas au-delà de la borne fixe (+300 s).

    python3 -m backtests.test_scan_timeout_race      # doit finir par « VÉRIFIÉ »
"""
from __future__ import annotations

import random
import sys

from alfred.botinstance import entry_scan_waits

B = 1_790_956_800                       # borne 4h (2026-10-02 16:00 UTC)
SYMS = {f"T{i}" for i in range(10)}
TICK = 20


def legacy_waits(now, last_4h_close, cooldowns, held, trade_symbols):
    deadline = last_4h_close + 300
    return {s: e for s, e in cooldowns.items()
            if s in trade_symbols and s not in held and now < e <= deadline}


def simulate(order, targets, phase, rule, extend=(), fails=()):
    held = dict(targets)
    t = B + phase
    while t <= B + 900:
        def close_step():
            for s, tgt in list(held.items()):
                if t >= tgt:
                    if s in extend:
                        held[s] = tgt + 12 * 3600
                    elif s not in fails:
                        del held[s]

        def scan_step():
            if not rule(t, B, {}, held, SYMS):
                return (t, frozenset(s for s, tg in held.items() if tg <= t), frozenset(held))
            return None

        if order == "exits_first":
            close_step()
            res = scan_step()
        else:
            res = scan_step()
            if res is None:
                close_step()
        if res:
            return res
        t += TICK
    raise AssertionError("le scan n'a jamais eu lieu")


def main():
    rng = random.Random(20261005)
    failures, legacy_hits = [], 0
    for case in range(3000):
        n = rng.randint(1, 3)
        targets = {f"T{i}": B + rng.uniform(150, 260) for i in range(n)}
        phase = rng.uniform(150, 240)
        a = simulate("exits_first", targets, phase, entry_scan_waits)
        b = simulate("scan_first", targets, phase, entry_scan_waits)
        if a[1] or b[1]:
            failures.append(("échu encore tenu au scan", case, a, b))
        if a[2] != b[2]:
            failures.append(("ordres divergents", case, a, b))
        if simulate("scan_first", targets, phase, legacy_waits)[1]:
            legacy_hits += 1
    # runner_ext : prolongée à l'échéance → ne doit pas bloquer le scan
    r = simulate("scan_first", {"T0": B + 200}, 180, entry_scan_waits, extend={"T0"})
    if r[0] > B + 200 + TICK or r[1]:
        failures.append(("runner_ext bloque", r))
    # fermeture qui échoue → le scan passe à la borne fixe, pas au-delà
    r = simulate("scan_first", {"T0": B + 200}, 180, entry_scan_waits, fails={"T0"})
    if r[0] > B + 300 + TICK:
        failures.append(("fermeture ratée bloque au-delà de la borne", r))
    print(f"3000 cas × 2 ordres — ancienne règle prise en défaut dans {legacy_hits} cas (attendu > 0)")
    if legacy_hits == 0:
        failures.append(("le test ne détecte pas l'ancienne règle",))
    for f in failures[:5]:
        print("ÉCHEC :", f)
    print("course timeout/scan : " + ("VÉRIFIÉ" if not failures else f"{len(failures)} ÉCHEC(S)"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
