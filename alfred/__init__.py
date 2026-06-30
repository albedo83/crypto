"""Alfred — multi-bot trading runtime for Hyperliquid.

Single-process architecture: one MarketDataMaster (websockets + residual REST)
feeding up to 8 BotInstances (paper or live), one unified web app.

Phase 1 scope: pure shared core (settings/models/features/signals/rules)
consumed by both the runtime and the backtests.
"""

ALFRED_VERSION = "1.6.10"
