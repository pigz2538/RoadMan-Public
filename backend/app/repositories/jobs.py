import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import JobRow
from ..domain.models import JobCreate, JobRecord, JobStatus


class JobRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, payload: JobCreate) -> JobRecord:
        record = JobRecord(kind=payload.kind, trip_id=payload.trip_id, payload=payload.payload)
        self.session.add(
            JobRow(
                id=record.id,
                trip_id=record.trip_id,
                kind=record.kind,
                status=record.status,
                progress=record.progress,
                payload_json=json.dumps(record.payload, ensure_ascii=False),
                result_json=None,
                error_json=None,
                cancel_requested=False,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )
        await self.session.commit()
        return record

    async def get(self, job_id: str) -> JobRecord | None:
        row = await self.session.get(JobRow, job_id)
        return self._to_record(row) if row else None

    async def active_for_trip(self, trip_id: str) -> JobRecord | None:
        """Return the newest queued/running planning job for one trip.

        The detail page can issue a second click while the first worker job is
        still running.  Allowing both jobs to write the same planning snapshot
        creates exactly the apparent 0→100→66% regressions users reported.
        Keep this guard at the database boundary so UI retries and API clients
        share the same single-flight behaviour.
        """
        result = await self.session.execute(
            select(JobRow)
            .where(
                JobRow.trip_id == trip_id,
                JobRow.kind == "planning",
                JobRow.status.in_([JobStatus.queued, JobStatus.running]),
            )
            .order_by(JobRow.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return self._to_record(row) if row else None

    async def cancel(self, job_id: str) -> JobRecord | None:
        row = await self.session.get(JobRow, job_id)
        if not row:
            return None
        row.cancel_requested = True
        if row.status == JobStatus.queued:
            row.status = JobStatus.cancelled
        row.updated_at = datetime.now(timezone.utc)
        await self.session.commit()
        return self._to_record(row)

    @staticmethod
    def _to_record(row: JobRow) -> JobRecord:
        return JobRecord(
            id=row.id,
            trip_id=row.trip_id,
            kind=row.kind,
            status=row.status,
            progress=row.progress,
            payload=json.loads(row.payload_json),
            result=json.loads(row.result_json) if row.result_json else None,
            error=json.loads(row.error_json) if row.error_json else None,
            cancel_requested=row.cancel_requested,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
