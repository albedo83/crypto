"""Feature-parity test: vectorized backtest features vs alfred.features
(Alfred phase 1e).

For N random (coin, candle-index) draws, compare the backtest's
build_features row against alfred.features.compute_features(candles[:i+1])
on every field consumed by signal detection:
    ret_24h (BT: ret_6h), ret_42h, drawdown, vol_z, vol_ratio, range_pct
plus vol_7d / vol_30d (vol_ratio inputs).

Usage:
    python3 -m backtests.test_feature_parity [N]
"""

from __future__ import annotations

import random
import sys

from alfred.features import compute_features
from backtests.backtest_genetic import load_3y_candles, build_features, TOKENS

FIELDS = [
    ("ret_24h", "ret_6h"),
    ("ret_42h", "ret_42h"),
    ("drawdown", "drawdown"),
    ("vol_z", "vol_z"),
    ("vol_ratio", "vol_ratio"),
    ("range_pct", "range_pct"),
    ("vol_7d", "vol_7d"),
    ("vol_30d", "vol_30d"),
]

REL_TOL = 1e-9
ABS_TOL = 1e-9


def close(a: float, b: float) -> bool:
    return abs(a - b) <= max(ABS_TOL, REL_TOL * max(abs(a), abs(b)))


def main() -> int:
    n_draws = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    random.seed(42)  # reproducible draws

    print("Loading data + building BT features...")
    data = load_3y_candles()
    bt_features = build_features(data)

    pool = []
    for coin in TOKENS:
        if coin in bt_features and coin in data:
            for row in bt_features[coin]:
                pool.append((coin, row))
    print(f"{len(pool)} feature rows across {len(bt_features)} coins")

    draws = random.sample(pool, min(n_draws, len(pool)))
    mismatches = 0
    none_count = 0
    for coin, row in draws:
        i = row["_idx"]
        bot_f = compute_features(data[coin][: i + 1])
        if bot_f is None:
            none_count += 1
            continue
        for bot_key, bt_key in FIELDS:
            a, b = bot_f[bot_key], row.get(bt_key, 0.0)
            if not close(a, b):
                mismatches += 1
                print(f"✗ {coin} idx={i} t={row['t']}: {bot_key} bot={a!r} bt={b!r}")
                break

    ok = len(draws) - mismatches - none_count
    print(f"\n{ok}/{len(draws)} draws identical "
          f"({mismatches} mismatches, {none_count} bot-None)")
    return 0 if mismatches == 0 and none_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
