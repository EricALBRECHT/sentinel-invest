from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "dev"

    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0

    jwt_secret_key: str = "dev-only-change-me-set-JWT_SECRET_KEY"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    web_cookie_secure: bool = False

    # Development placeholder. Replace with a real application name and contact
    # email before relying on SEC EDGAR; the SEC rejects anonymous clients.
    sec_user_agent: str = "Sentinel contact@example.com"
    sec_timeout_seconds: float = 30.0
    sec_max_retries: int = 3
    sec_min_interval_seconds: float = 0.2
    sec_sync_interval_hours: int = 24
    sec_sync_scan_hours: int = 6
    sec_sync_max_companies_per_run: int = 50
    universe_priority_interval_hours: int = 24
    universe_stale_days: int = 365
    universe_user_agent: str = (
        "SentinelResearchBot/1.0 (universe-importer; contact@example.com)"
    )
    universe_timeout_seconds: float = 30.0
    universe_max_retries: int = 3
    universe_min_interval_seconds: float = 1.0
    universe_bootstrap_max_per_run: int = 25
    universe_members_refresh_days: int = 7
    universe_bootstrap_scan_hours: int = 1
    market_provider: str = "yahoo"
    market_user_agent: str = "Sentinel market-data contact@example.com"
    market_timeout_seconds: float = 30.0
    market_max_retries: int = 3
    market_min_interval_seconds: float = 1.0
    market_history_years: int = 10
    market_sync_interval_hours: int = 24
    market_sync_scan_hours: int = 24
    market_sync_max_companies_per_run: int = 200
    intelligence_user_agent: str = "Sentinel news-collector contact@example.com"
    intelligence_timeout_seconds: float = 20.0
    intelligence_max_retries: int = 3
    intelligence_min_interval_seconds: float = 1.0
    intelligence_lookback_days: int = 7
    intelligence_scan_hours: int = 2
    intelligence_max_companies_per_run: int = 20
    intelligence_max_text_chars: int = 20000
    supply_chain_scan_hours: int = 24
    supply_chain_portfolio_interval_hours: int = 24
    supply_chain_deep_interval_hours: int = 24
    supply_chain_watched_interval_hours: int = 48
    supply_chain_max_companies_per_run: int = 20
    supply_chain_lookback_days: int = 30
    discovery_verify_max_per_run: int = 20
    discovery_verify_scan_hours: int = 24
    discovery_verify_min_confidence: int = 75
    discovery_max_depth: int = 3
    discovery_expansion_max_per_run: int = 20
    discovery_expansion_scan_hours: int = 24
    discovery_expansion_analyzed_hours: int = 48
    ai_provider: str = "local"
    ai_model_name: str = "Qwen2.5-1.5B-Instruct-Q4_K_M"
    ai_model_path: str = ""
    ai_model_version: str = ""
    ai_model_cache: str = "/models"
    ai_model_filename: str = ""
    ai_max_context: int = 2048
    ai_max_output_tokens: int = 768
    ai_max_input_chars: int = 12000
    ai_temperature: float = 0.0
    ai_gpu_layers: int = 20

    class Config:
        env_file = ".env"


settings = Settings()

