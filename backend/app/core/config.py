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
    load_local_skill_credentials: bool = True
    cors_origins: str = "http://localhost:5173"
    upload_dir: str = "./data/uploads"
    max_upload_bytes: int = 20 * 1024 * 1024
    allowed_upload_extensions: str = ".png,.jpg,.jpeg,.webp,.pdf"
    allowed_upload_mime_types: str = "image/png,image/jpeg,image/webp,application/pdf"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.amap_webservice_key and settings.load_local_skill_credentials:
        local_key = Path(__file__).resolve().parents[3] / "Skills" / "amap-lbs" / "apipkey.txt"
        if local_key.is_file():
            settings.amap_webservice_key = local_key.read_text(encoding="utf-8").strip()
    return settings
