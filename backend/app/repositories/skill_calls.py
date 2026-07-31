import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select

from ..db import SessionLocal, SkillCallRow
from ..domain.models import SkillCallRecord, SkillResult
from ..skills.base import SkillContext


async def record_skill_call(
    adapter: str,
    result: SkillResult,
    context: SkillContext,
) -> None:
    async with SessionLocal() as session:
        session.add(
            SkillCallRow(
                id=f"skillcall_{uuid4().hex[:12]}",
                request_id=context.request_id,
                trip_id=context.trip_id,
                adapter=adapter,
                provider=result.provider,
                success=result.success,
                cache_hit=result.cache_hit,
                latency_ms=result.latency_ms,
                error_code=result.error_code,
                source_summary_json=json.dumps(
                    [
                        {"provider": source.provider, "title": source.title}
                        for source in result.sources
                    ],
                    ensure_ascii=False,
                ),
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


class SkillCallRepository:
    async def list(self, limit: int = 50) -> list[SkillCallRecord]:
        async with SessionLocal() as session:
            statement = (
                select(SkillCallRow)
                .order_by(SkillCallRow.created_at.desc())
                .limit(limit)
            )
            rows = (await session.scalars(statement)).all()
        return [
            SkillCallRecord(
                id=row.id,
                request_id=row.request_id,
                trip_id=row.trip_id,
                adapter=row.adapter,
                provider=row.provider,
                success=row.success,
                cache_hit=row.cache_hit,
                latency_ms=row.latency_ms,
                error_code=row.error_code,
                source_summary=json.loads(row.source_summary_json),
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def summary(self) -> dict:
        async with SessionLocal() as session:
            total = await session.scalar(select(func.count()).select_from(SkillCallRow))
            successful = await session.scalar(
                select(func.count()).select_from(SkillCallRow).where(SkillCallRow.success.is_(True))
            )
            cached = await session.scalar(
                select(func.count()).select_from(SkillCallRow).where(SkillCallRow.cache_hit.is_(True))
            )
            average_latency = await session.scalar(select(func.avg(SkillCallRow.latency_ms)))
            rows = (
                await session.execute(
                    select(SkillCallRow.adapter, func.count())
                    .group_by(SkillCallRow.adapter)
                    .order_by(func.count().desc())
                )
            ).all()
        total_value = int(total or 0)
        return {
            "total_calls": total_value,
            "successful_calls": int(successful or 0),
            "failed_calls": max(0, total_value - int(successful or 0)),
            "cache_hits": int(cached or 0),
            "average_latency_ms": round(float(average_latency or 0), 2),
            "by_adapter": {adapter: int(count) for adapter, count in rows},
            "estimated_cost_usd": None,
            "cost_note": "当前外部 Skill 未返回可计费 token，保留调用量与延迟统计。",
        }
