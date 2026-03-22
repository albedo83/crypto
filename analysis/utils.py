"""Shared constants, session definitions, and plot helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import os

# ── Instruments (must match DB) ──────────────────────────────────────
INSTRUMENT_IDS = {
    "BTCUSDT": 1,
    "ETHUSDT": 2,
    "ADAUSDT": 3,
}
ID_TO_SYMBOL = {v: k for k, v in INSTRUMENT_IDS.items()}

# ── Data range (clean 3.5d + ongoing) ───────────────────────────────
DATA_START = datetime(2025, 3, 15, tzinfo=timezone.utc)
DATA_END = datetime(2025, 3, 19, tzinfo=timezone.utc)

# ── Trading sessions (UTC hours) ────────────────────────────────────
SESSIONS = {
    "asian":     (0, 8),
    "european":  (8, 14),
    "us":        (14, 21),
    "overnight": (21, 24),
}


def session_label(hour: int) -> str:
    """Map UTC hour to session name."""
    for name, (start, end) in SESSIONS.items():
        if start <= hour < end:
            return name
    return "overnight"


def add_session_column(df, ts_col: str = "bucket") -> None:
    """Add 'session' column in-place based on UTC hour."""
    df["session"] = df[ts_col].dt.hour.map(session_label)


# ── Output helpers ───────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def savefig(name: str) -> str:
    """Save current figure and return path."""
    path = os.path.join(OUTPUT_DIR, name)
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  → saved {path}")
    return path


# ── Dark theme ───────────────────────────────────────────────────────
def apply_dark_theme() -> None:
    plt.style.use("dark_background")
    plt.rcParams.update({
        "figure.figsize": (14, 7),
        "axes.grid": True,
        "grid.alpha": 0.3,
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.labelsize": 12,
    })


# ── Stats helpers ────────────────────────────────────────────────────
def spearman_rho(x, y):
    """Spearman rank correlation + p-value."""
    from scipy.stats import spearmanr
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 30:
        return np.nan, np.nan
    return spearmanr(x[mask], y[mask])


def quintile_stats(df, signal_col: str, return_col: str, n_quantiles: int = 5):
    """Group by quantile of signal_col, return mean of return_col per bucket."""
    df = df.dropna(subset=[signal_col, return_col]).copy()
    df["quantile"] = pd.qcut(df[signal_col], n_quantiles, labels=False, duplicates="drop")
    return df.groupby("quantile")[return_col].agg(["mean", "std", "count"])


# Lazy import for pd in quintile_stats
import pandas as pd
