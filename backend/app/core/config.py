from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./roadman.db"
    redis_url: str = "redis://localhost:6379/0"
    skill_cache_prefix: str = "roadman:skill-cache"
    redis_connect_timeout_seconds: float = 0.35
    enable_job_queue: bool = True
    amap_webservice_key: str = ""
    opentripmap_api_key: str = ""
    flyai_api_key: str = ""
    # Optional public-data fallbacks.  They are deliberately opt-in for
    # credentialed services; the train endpoint is usable without a key.
    train_fallback_url: str = "https://api.lolimi.cn/API/hc/api"
    mcp_12306_url: str = ""
    flight_fallback_url: str = "https://open.6api.net/flight/getTicket"
    flight_fallback_api_key: str = ""
    oil_api_url: str = "https://www.mxnzp.com/api/oil/search"
    oil_app_id: str = ""
    oil_app_secret: str = ""
    load_local_skill_credentials: bool = True
    cors_origins: str = "http://localhost:5173"
    upload_dir: str = "./data/uploads"
    max_upload_bytes: int = 20 * 1024 * 1024
    allowed_upload_extensions: str = ".png,.jpg,.jpeg,.webp,.pdf,.docx,.md,.xlsx"
    allowed_upload_mime_types: str = (
        "image/png,image/jpeg,image/webp,application/pdf,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "text/markdown,text/plain,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    # DeepSeek is the single semantic-agent provider.  The API is OpenAI
    # compatible, but we keep the provider-specific fields explicit so that
    # deployment configuration and diagnostics cannot silently point at the
    # former Ollama endpoint.
    deepseek_api_url: str = "https://api.deepseek.com/chat/completions"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"
    # Fast non-thinking mode is the product default.  A deployment can opt
    # into deeper reasoning for a specific evaluation run, but ordinary
    # planning and fallback lookups must not silently wait on it.
    deepseek_reasoning_effort: str = "low"
    deepseek_thinking: bool = False
    deepseek_max_tokens: int = 32768
    deepseek_timeout_seconds: float = 180

    # Deprecated compatibility inputs.  Existing local test fixtures and
    # installations may still pass OLLAMA_*; the validator below maps them to
    # DeepSeek and then exposes the canonical values through these attributes.
    ollama_api_url: str = ""
    ollama_api_key: str = ""
    ollama_model: str = ""
    ollama_timeout_seconds: float = 90
    enable_llm_requirement_extraction: bool = True
    enable_poi_web_enrichment: bool = True
    enable_route_elevation: bool = False
    poi_web_timeout_seconds: float = 1.8
    max_clarification_rounds: int = 3
    planning_state_ttl_seconds: int = 24 * 60 * 60
    file_retention_days: int = 30
    enable_rate_limit: bool = True
    rate_limit_per_minute: int = 600

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def _migrate_legacy_llm_settings(self) -> "Settings":
        """Map legacy Ollama settings without allowing a stale endpoint.

        The application used to call Ollama's ``/api/generate`` contract.
        Keeping these input names readable avoids breaking older test/config
        files, while every runtime request now uses the DeepSeek Chat
        Completions contract and the official ``deepseek-v4-flash`` model.
        """
        if not self.deepseek_api_key and self.ollama_api_key:
            self.deepseek_api_key = self.ollama_api_key
        if self.ollama_model and self.deepseek_model == "deepseek-v4-flash":
            legacy_model = self.ollama_model.strip()
            self.deepseek_model = (
                "deepseek-v4-flash"
                if legacy_model.startswith("deepseek-v4-flash:")
                else legacy_model
            )
        if self.ollama_timeout_seconds != 90 and self.deepseek_timeout_seconds == 180:
            self.deepseek_timeout_seconds = self.ollama_timeout_seconds

        # Compatibility readers see the effective provider values.  No call
        # site should depend on these names; they are intentionally aliases.
        self.ollama_api_url = self.deepseek_api_url
        self.ollama_api_key = self.deepseek_api_key
        self.ollama_model = self.deepseek_model
        self.ollama_timeout_seconds = self.deepseek_timeout_seconds
        return self


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.amap_webservice_key and settings.load_local_skill_credentials:
        local_key = Path(__file__).resolve().parents[3] / "Skills" / "amap-lbs" / "apipkey.txt"
        if local_key.is_file():
            settings.amap_webservice_key = local_key.read_text(encoding="utf-8").strip()
    if not settings.opentripmap_api_key and settings.load_local_skill_credentials:
        local_key = Path(__file__).resolve().parents[3] / "Skills" / "opentripmap" / "apikey.txt"
        if local_key.is_file():
            settings.opentripmap_api_key = local_key.read_text(encoding="utf-8").strip()
    return settings
