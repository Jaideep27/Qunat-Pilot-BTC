from pathlib import Path
import hashlib
import json
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='QUANTPILOT_', env_file='.env', extra='ignore')
    data_dir: Path = Path('data')
    artifact_dir: Path = Path('artifacts')
    database_url: str = 'sqlite:///data/quantpilot.db'
    market_database_url: str | None = None
    market_history_years: int = Field(5,ge=1,le=15)
    market_poll_seconds: float = Field(5,ge=1,le=60)
    worker_max_age_seconds: int = Field(1800,ge=900,le=7200)
    news_poll_seconds: int = Field(300, ge=60, le=3600)
    news_daily_batches: int = Field(48, ge=1, le=200)
    news_model: str = 'gpt-6-luna'
    news_codex_command: str = 'codex'
    news_timeout_seconds: int = Field(120, ge=10, le=300)
    cors_origins: list[str] = ['http://localhost:5173']

class RiskConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='QUANTPILOT_RISK_', allow_inf_nan=False)
    version: str = 'risk-v1'
    starting_balance: float = Field(10000, gt=0)
    max_exposure: float = Field(.25, gt=0, le=1)
    risk_budget: float = Field(.0025, gt=0, le=.05)
    stop_atr: float = Field(1.5, gt=0)
    max_hold_minutes: int = Field(60, ge=5, le=1440)
    daily_loss: float = Field(.02, gt=0, lt=1)
    threshold: float = Field(.60, ge=.5, lt=1)
    fee_bps: float = Field(5, ge=0)
    slippage_bps: float = Field(3, ge=0)
    buffer_bps: float = Field(5, ge=0)
    maintenance_margin: float = Field(.005, gt=0, lt=1)
    execution_delay_seconds: int = Field(1, ge=1)
    max_age_seconds: int = Field(600, ge=1)

    @property
    def identity(self):
        digest = hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()[:10]
        return self.version+":"+digest

settings = Settings()
