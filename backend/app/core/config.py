from functools import lru_cache
from pathlib import Path

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
    ollama_api_url: str = "https://ollama.com/api/generate"
    ollama_api_key: str = ""
    ollama_model: str = "deepseek-v4-flash:cloud"
    ollama_timeout_seconds: float = 90
    enable_llm_requirement_extraction: bool = True
    max_clarification_rounds: int = 3
    planning_state_ttl_seconds: int = 24 * 60 * 60
    file_retention_days: int = 30
    enable_rate_limit: bool = True
    rate_limit_per_minute: int = 600

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


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
