"""Runtime configuration.

Every value that was hard-coded in the n8n Set node ("Map Ticket Fields1")
lives here, read from environment variables / a .env file.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- STOCRM (was: stocrm_host / sid / board_id / source_id) ----------
    stocrm_host: str = "okolomota.stocrm.ru"
    stocrm_sid: str = ""
    stocrm_board_id: int = 1277
    stocrm_source_id: int = 4
    stocrm_timeout: float = 30.0

    # ---------- OpenRouter (was: OpenRouter Chat Model1 credential) ----------
    openrouter_api_key: str = ""
    openrouter_model: str = "deepseek/deepseek-v4-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_timeout: float = 120.0
    # n8n: retryOnFail = true, waitBetweenTries = 2000 ms
    llm_max_attempts: int = 3
    llm_retry_delay: float = 2.0

    # ---------- Webhook security (optional, OFF unless you set a secret) ----------
    # The caller sends x-yapogovoru-signature: sha256=<hmac of the raw body>
    shared_secret: str | None = None

    # ---------- Behavior ----------
    # The n8n Set node takes the phone from the LLM output only, so this
    # defaults to False to keep the original behavior. Turn it on if you
    # would rather fall back to body.phone_number than skip the ticket.
    phone_fallback_to_caller_id: bool = False
    # Dry run: log the STOCRM payload instead of sending it.
    dry_run: bool = False

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
