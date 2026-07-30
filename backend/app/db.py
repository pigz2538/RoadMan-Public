from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .core.config import get_settings


class Base(DeclarativeBase):
    pass


class TimestampColumns:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TripRow(TimestampColumns, Base):
    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    document: Mapped[str] = mapped_column(Text)
    state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    messages_json: Mapped[str] = mapped_column(Text, default="[]")


class VehicleRow(TimestampColumns, Base):
    __tablename__ = "vehicles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    brand: Mapped[str] = mapped_column(String(100), index=True)
    series: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(150))
    power_type: Mapped[str] = mapped_column(String(32), index=True)
    document: Mapped[str] = mapped_column(Text)


class FileRow(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trip_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    storage_path: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(150))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class JobRow(TimestampColumns, Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trip_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)


class SkillCallRow(Base):
    __tablename__ = "skill_calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    trip_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    adapter: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(100))
    success: Mapped[bool] = mapped_column(Boolean, index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    source_summary_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_tables() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def get_session():
    async with SessionLocal() as session:
        yield session
