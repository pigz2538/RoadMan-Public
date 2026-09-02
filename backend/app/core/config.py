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
    aviationstack_url: str = "https://api.aviationstack.com/v1/flights"
    aviationstack_api_key: str = ""
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
    # All semantic agents use this provider-neutral contract.  The endpoint,
    # credential, model and wire format are configuration, not agent/source
    # code.  ``openai`` is the default because most hosted providers expose
    # the same Chat Completions response shape; ``ollama_generate`` remains a
    # compatibility option for an Ollama native /api/generate endpoint.
    llm_provider: str = "configured"
    llm_api_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_api_style: str = ""
    llm_timeout_seconds: float = 0
    llm_thinking: bool | None = None
    llm_max_tokens: int = 0

    # Legacy Ollama names are accepted as an external configuration migration
    # path.  They are never required by the planner and are normalized to the
    # provider-neutral values above.
    ollama_api_url: str = ""
    ollama_api_key: str = ""
    ollama_model: str = ""
    ollama_timeout_seconds: float = 0
    ollama_thinking: bool = False
    ollama_max_tokens: int = 0

    # Deprecated DeepSeek names are accepted only as configuration aliases so
    # older .env files and integrations do not crash.  They are considered
    # only when the canonical LLM_* value is absent.
    deepseek_api_url: str = ""
    deepseek_api_key: str = ""
    deepseek_model: str = ""
    deepseek_reasoning_effort: str = ""
    deepseek_thinking: bool = False
    deepseek_max_tokens: int = 32768
    deepseek_timeout_seconds: float = 180
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
        """Resolve one provider-neutral LLM configuration with legacy aliases."""
        provider = str(self.llm_provider or "configured").strip().lower()
        self.llm_provider = provider or "configured"

        # Canonical values win.  A legacy key is only a migration fallback,
        # which lets an existing ignored .env keep working after deployment.
        self.llm_api_key = str(
            self.llm_api_key or self.ollama_api_key or self.deepseek_api_key or ""
        ).strip()
        explicit_llm_url = str(self.llm_api_url or "").strip()
        legacy_ollama_url = str(self.ollama_api_url or "").strip()
        legacy_deepseek_url = str(self.deepseek_api_url or "").strip()
        configured_url = str(
            explicit_llm_url or legacy_ollama_url or legacy_deepseek_url or ""
        ).strip()
        self.llm_api_url = configured_url

        self.llm_model = str(
            self.llm_model or self.ollama_model or self.deepseek_model or ""
        ).strip()

        # If an old deployment explicitly selected Ollama's native endpoint,
        # preserve that wire format.  Otherwise every request is OpenAI-shaped.
        explicit_style = str(self.llm_api_style or "").strip().lower()
        style = explicit_style or "openai"
        if style not in {"openai", "ollama_generate"}:
            style = "openai"
        if (
            not explicit_style
            and not explicit_llm_url
            and legacy_ollama_url.rstrip("/").endswith("/api/generate")
        ):
            style = "ollama_generate"
        self.llm_api_style = style

        legacy_timeout = self.deepseek_timeout_seconds
        configured_timeout = self.llm_timeout_seconds or self.ollama_timeout_seconds
        if not configured_timeout or configured_timeout <= 0:
            configured_timeout = legacy_timeout if legacy_timeout != 180 else 90
        self.llm_timeout_seconds = float(configured_timeout)
        if self.llm_timeout_seconds <= 0:
            self.llm_timeout_seconds = 90

        if self.llm_thinking is None:
            self.llm_thinking = bool(self.ollama_thinking or self.deepseek_thinking)
        else:
            self.llm_thinking = bool(self.llm_thinking)
        configured_max_tokens = (
            self.llm_max_tokens or self.ollama_max_tokens or self.deepseek_max_tokens
        )
        self.llm_max_tokens = max(256, int(configured_max_tokens or 32768))

        # Compatibility readers see the effective canonical values.  These
        # aliases keep old integrations/tests source-compatible while all new
        # requests use llm_* fields.
        self.ollama_api_url = self.llm_api_url
        self.ollama_api_key = self.llm_api_key
        self.ollama_model = self.llm_model
        self.ollama_timeout_seconds = self.llm_timeout_seconds
        self.ollama_thinking = self.llm_thinking
        self.ollama_max_tokens = self.llm_max_tokens
        self.deepseek_api_url = self.llm_api_url
        self.deepseek_api_key = self.llm_api_key
        self.deepseek_model = self.llm_model
        self.deepseek_timeout_seconds = self.llm_timeout_seconds
        self.deepseek_thinking = self.llm_thinking
        self.deepseek_max_tokens = self.llm_max_tokens
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
