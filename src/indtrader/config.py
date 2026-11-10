"""Env-driven config. Secrets only ever live in env / .env (git-ignored)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- AI providers: switchable, all optional ---
    # Router tries providers in this order; unknown/unconfigured names are skipped.
    ai_provider_order: str = "ollama,none"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-haiku-latest"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"
    grok_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    # --- Market data ---
    data_provider_order: str = "nseapi,yfinance"

    # --- Safety ---
    trading_mode: str = "paper"  # paper | live ; anything else fails closed to paper
    data_dir: Path = Field(default=Path("data"))

    # --- Alerts (Phase 4) ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def ai_order(self) -> list[str]:
        return [p.strip().lower() for p in self.ai_provider_order.split(",") if p.strip()]

    @property
    def execution_mode(self) -> str:
        return "paper" if self.trading_mode.strip().lower() != "live" else "live"


@lru_cache
def get_settings() -> Settings:
    return Settings()
