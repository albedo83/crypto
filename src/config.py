from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "crypto"
    db_user: str = "crypto"
    db_password: str = "crypto_pwd_2024"
    db_min_pool: int = 2
    db_max_pool: int = 20

    # Venue
    venue_name: str = "binance_futures"
    ws_base_url: str = "wss://fstream.binance.com"
    rest_base_url: str = "https://fapi.binance.com"

    # Symbols
    symbols: str = "BTCUSDT,ETHUSDT,ADAUSDT"

    # Collector
    collector_id: str = "collector-01"
    batch_flush_ms: int = 250
    batch_max_size: int = 500
    queue_max_size: int = 10000
    heartbeat_interval_s: int = 5

    # Dashboard
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8090

    # Logging
    log_level: str = "INFO"

    @property
    def dsn(self) -> str:
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]

    @property
    def ws_combined_url(self) -> str:
        streams = []
        for sym in self.symbol_list:
            s = sym.lower()
            streams.extend([
                f"{s}@aggTrade",
                f"{s}@bookTicker",
                f"{s}@depth10@100ms",
                f"{s}@markPrice@1s",
                f"{s}@forceOrder",
            ])
        return f"{self.ws_base_url}/stream?streams={'/'.join(streams)}"


settings = Settings()
