"""Walk-forward strict 4/4 — per-trade notional cap sweep for SMALL accounts (<$100).

Question : un nouveau bot `baby` à capital <$100 est mécaniquement viable (min
ordre HL $10, base_size planché à $10), mais le cap notionnel `max_notional_per_trade`
($500 dans Params) est DORMANT sous ~$850 de capital. À $80, un seul S9 (~$67) ou
S5 (~$43) sature la marge dispo (2× capital) avant de remplir les 6 slots →
diversification étranglée, S8/S9 manqués (cf. backlog c1f4901, 2026-06-14).

Hypothèse : un cap notionnel BAS écrête ces gros trades → libère des slots →
meilleure capture S8/S9 à petit capital. La zone < $500 n'a jamais été validée.

Sémantique ALIGNED (phase 6) — appel canonique IDENTIQUE à backtest_rolling.py :
  aligned=True, early_exit_params/runner_extension=None (toutes les règles d'exit
  courantes — traj_cut, s8_dead_in_water, s8_inlife, dead_timeout, runner_ext —
  sont dans evaluate_exit via alfred.rules), apply_adaptive_modulator=True,
  margin_check=True (matérialise la contention de slots — INDISPENSABLE ici).

En aligned, rules.position_size applique le cap INTERNE _P.max_notional ($500,
dormant à $80). Le paramètre `max_notional_per_trade` de run_window s'applique
PAR-DESSUS (ligne ~1449) → c'est lui qui mord à petit capital. Baseline = None
(= config live actuelle à $80, $500 interne dormant).

Configs :
  A = baseline (None = cap $500 interne dormant, comportement live actuel)
  B = cap $50
  C = cap $40
  D = cap $30

Capitaux testés : $80 (primaire) et $100 (robustesse).

Critère strict : vs baseline, ΔPnL ≥ 0 ET ΔDD ≤ +2pp sur LES 4 fenêtres.
DD prioritaire (un petit compte ne supporte pas un gros drawdown).

4 splits 6m non-recouvrants ancrés sur la donnée la plus récente :
  split_1: T−24m → T−18m   split_2: T−18m → T−12m
  split_3: T−12m → T−6m    split_4: T−6m  → T
"""
from backtests.backtest_rolling import run_window, load_oi, load_funding, load_dxy
from backtests.backtest_genetic import load_3y_candles, build_features
from backtests.backtest_sector import compute_sector_features

print("Loading data...")
data = load_3y_candles()
features = build_features(data)
sector_features = compute_sector_features(features, data)
dxy = load_dxy()
oi = load_oi()
funding = load_funding()
print("Done.")

latest_ts = max(c["t"] for c in data["BTC"])
SIX_M_MS = 6 * 30 * 24 * 3600 * 1000

splits = [
    ("split_1 (24m→18m)", latest_ts - 4 * SIX_M_MS, latest_ts - 3 * SIX_M_MS),
    ("split_2 (18m→12m)", latest_ts - 3 * SIX_M_MS, latest_ts - 2 * SIX_M_MS),
    ("split_3 (12m→6m) ", latest_ts - 2 * SIX_M_MS, latest_ts - 1 * SIX_M_MS),
    ("split_4 (6m→now) ", latest_ts - 1 * SIX_M_MS, latest_ts),
]

# (label, max_notional_per_trade). None = baseline (cap $500 interne dormant à <$100).
configs = [
    ("A_baseline", None),
    ("B_cap50",    50.0),
    ("C_cap40",    40.0),
    ("D_cap30",    30.0),
]

CAPITALS = [80.0, 100.0]
BASELINE = "A_baseline"


def run(start_ts, end_ts, cap_amount, notional_cap):
    """Un backtest aligned sur une (fenêtre, capital, cap notionnel)."""
    r = run_window(features, data, sector_features, dxy, start_ts, end_ts,
                   start_capital=cap_amount,
                   oi_data=oi, funding_data=funding,
                   apply_adaptive_modulator=True,
                   aligned=True,
                   max_notional_per_trade=notional_cap,
                   margin_check=True)
    bs = r["by_strat"]
    # Take = trades S8+S9 effectivement pris (preuve de libération de slots).
    s8s9 = sum(bs.get(s, {}).get("n", 0) for s in ("S8", "S9"))
    return {
        "pnl_pct": r["pnl_pct"],
        "dd_pct": r["max_dd_pct"],
        "n_trades": r["n_trades"],
        "margin_skips": r["n_margin_skip"],
        "s8s9_trades": s8s9,
    }


for cap_amount in CAPITALS:
    print(f"\n\n################  START CAPITAL = ${cap_amount:.0f}  ################")
    print(f"\n{'Split':>20} | {'Config':>11} | {'PnL%':>8} | {'DD%':>7} | "
          f"{'Trades':>6} | {'S8+S9':>5} | {'MgnSkip':>7} | {'ΔPnL pp':>8} | {'ΔDD pp':>7}")
    print("-" * 120)

    results = {split[0]: {} for split in splits}
    for split_label, start_ts, end_ts in splits:
        for cfg_label, notional_cap in configs:
            results[split_label][cfg_label] = run(start_ts, end_ts, cap_amount, notional_cap)
        base = results[split_label][BASELINE]
        for cfg_label, _ in configs:
            r = results[split_label][cfg_label]
            d_pnl = r["pnl_pct"] - base["pnl_pct"]
            d_dd = r["dd_pct"] - base["dd_pct"]
            print(f"{split_label:>20} | {cfg_label:>11} | {r['pnl_pct']:>+8.2f} | "
                  f"{r['dd_pct']:>+7.2f} | {r['n_trades']:>6} | {r['s8s9_trades']:>5} | "
                  f"{r['margin_skips']:>7} | {d_pnl:>+8.2f} | {d_dd:>+7.2f}")
        print("-" * 120)

    print(f"\n=== STRICT 4/4 VERDICT @ ${cap_amount:.0f} (vs {BASELINE}) ===")
    # max_dd_pct est SIGNÉ négatif (drawdown -42% = -42.0, plus négatif = pire).
    # « DD ne se dégrade pas de plus de 2pp » ⇒ config_dd − baseline_dd ≥ −2.0.
    print("Critère : ΔPnL ≥ 0 ET ΔDD ≥ −2pp (DD pas pire de +2pp) sur LES 4 splits\n")
    print(f"{'Config':>11} | {'Splits ΔPnL+':>13} | {'Splits ΔDD≥−2pp':>16} | "
          f"{'Tot skips':>9} | {'Tot S8+S9':>9} | {'Verdict':>22}")
    print("-" * 100)
    for cfg_label, _ in configs:
        if cfg_label == BASELINE:
            continue
        pnl_pass = sum(1 for sl in splits
                       if results[sl[0]][cfg_label]["pnl_pct"]
                       - results[sl[0]][BASELINE]["pnl_pct"] >= 0)
        dd_pass = sum(1 for sl in splits
                      if results[sl[0]][cfg_label]["dd_pct"]
                      - results[sl[0]][BASELINE]["dd_pct"] >= -2.0)
        skips = sum(results[sl[0]][cfg_label]["margin_skips"] for sl in splits)
        s8s9 = sum(results[sl[0]][cfg_label]["s8s9_trades"] for sl in splits)
        verdict = "STRICT 4/4 PASS" if (pnl_pass == 4 and dd_pass == 4) \
            else f"FAIL ({pnl_pass}/4 ΔPnL, {dd_pass}/4 ΔDD)"
        print(f"{cfg_label:>11} | {pnl_pass:>13} | {dd_pass:>16} | "
              f"{skips:>9} | {s8s9:>9} | {verdict:>22}")
    # Baseline S8+S9 reference (combien de slots S8/S9 le baseline manque-t-il ?)
    base_s8s9 = sum(results[sl[0]][BASELINE]["s8s9_trades"] for sl in splits)
    base_skips = sum(results[sl[0]][BASELINE]["margin_skips"] for sl in splits)
    print(f"{BASELINE:>11} | {'—':>13} | {'—':>16} | {base_skips:>9} | {base_s8s9:>9} | {'(référence)':>22}")
