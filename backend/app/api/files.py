import asyncio
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.errors import AppError
from ..db import get_session
from ..domain.models import (
    AttachmentConfirmation,
    AttachmentExtraction,
    FileRecord,
    PlaceRef,
)
from ..repositories import FileRepository, TripRepository
from ..services.attachments import extract_attachment

router = APIRouter(prefix="/api/v1/files", tags=["files"])
settings = get_settings()


def get_repo(session: AsyncSession = Depends(get_session)) -> FileRepository:
    return FileRepository(session)


@router.post("", response_model=FileRecord, status_code=status.HTTP_201_CREATED)
async def upload_file(
    upload: UploadFile = File(...),
    trip_id: str | None = Form(default=None),
    repo: FileRepository = Depends(get_repo),
) -> FileRecord:
    original_name = Path(upload.filename or "upload").name
    suffix = Path(original_name).suffix.lower()
    allowed_extensions = {item.strip() for item in settings.allowed_upload_extensions.split(",")}
    allowed_mime = {item.strip() for item in settings.allowed_upload_mime_types.split(",")}
    mime_type = (upload.content_type or "application/octet-stream").lower()
    if suffix == ".md" and mime_type == "text/plain":
        mime_type = "text/markdown"
    if suffix not in allowed_extensions or mime_type not in allowed_mime:
        raise AppError(
            "FILE_TYPE_NOT_ALLOWED",
            "仅支持图片、PDF、DOCX、Markdown 和 XLSX",
            415,
            {"extension": suffix, "mime_type": mime_type},
        )
    content = await upload.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise AppError(
            "FILE_TOO_LARGE",
            "上传文件超过大小限制",
            413,
            {"max_bytes": settings.max_upload_bytes},
        )
    detected_mime = _detect_mime(content, suffix)
    if detected_mime != mime_type:
        raise AppError(
            "FILE_CONTENT_INVALID",
            "文件内容与声明类型不一致",
            415,
            {"declared_mime": mime_type, "detected_mime": detected_mime},
        )
    upload_dir = Path(settings.upload_dir).resolve()
    await asyncio.to_thread(upload_dir.mkdir, parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{suffix}"
    storage_path = upload_dir / stored_name
    await asyncio.to_thread(storage_path.write_bytes, content)
    record = FileRecord(
        trip_id=trip_id,
        original_name=original_name,
        stored_name=stored_name,
        mime_type=mime_type,
        size_bytes=len(content),
    )
    return await repo.create(record, str(storage_path))


@router.get("/{file_id}", response_model=FileRecord)
async def get_file_metadata(
    file_id: str,
    repo: FileRepository = Depends(get_repo),
) -> FileRecord:
    record = await repo.get(file_id)
    if not record:
        raise AppError("FILE_NOT_FOUND", "文件不存在", 404, {"file_id": file_id})
    return record


@router.get("/{file_id}/content")
async def download_file(
    file_id: str,
    repo: FileRepository = Depends(get_repo),
) -> FileResponse:
    row = await repo.get_row(file_id)
    if not row or not Path(row.storage_path).is_file():
        raise AppError("FILE_NOT_FOUND", "文件不存在", 404, {"file_id": file_id})
    return FileResponse(
        row.storage_path,
        media_type=row.mime_type,
        filename=row.original_name,
    )


@router.post("/{file_id}/extract", response_model=AttachmentExtraction)
async def extract_file_preview(
    file_id: str,
    session: AsyncSession = Depends(get_session),
) -> AttachmentExtraction:
    repo = FileRepository(session)
    row = await repo.get_row(file_id)
    if not row or not Path(row.storage_path).is_file():
        raise AppError("FILE_NOT_FOUND", "文件不存在", 404, {"file_id": file_id})
    extraction = await extract_attachment(
        file_id,
        Path(row.storage_path),
        row.mime_type,
        settings,
    )
    if row.trip_id:
        trip_repo = TripRepository(session)
        trip = await trip_repo.get(row.trip_id)
        if trip:
            state, markdown = await trip_repo.get_planning_snapshot(row.trip_id)
            state = state or {}
            state.setdefault("attachment_previews", {})[file_id] = extraction.model_dump(
                mode="json"
            )
            await trip_repo.save_planning_result(trip, state, markdown)
    return extraction


@router.post("/{file_id}/confirm", response_model=AttachmentExtraction)
async def confirm_file_extraction(
    file_id: str,
    payload: AttachmentConfirmation,
    session: AsyncSession = Depends(get_session),
) -> AttachmentExtraction:
    row = await FileRepository(session).get_row(file_id)
    if not row or not row.trip_id:
        raise AppError("FILE_TRIP_REQUIRED", "附件尚未关联行程", 409)
    trip_repo = TripRepository(session)
    trip = await trip_repo.get(row.trip_id)
    state, markdown = await trip_repo.get_planning_snapshot(row.trip_id)
    if not trip:
        raise AppError("TRIP_NOT_FOUND", "行程不存在", 404)
    state = state or {}
    raw = state.get("attachment_previews", {}).get(file_id)
    if not raw:
        raise AppError("ATTACHMENT_PREVIEW_REQUIRED", "请先解析并检查附件内容", 409)
    extraction = AttachmentExtraction.model_validate(raw)
    # The confirmation payload keeps its historical ``accepted_places`` name,
    # but an Agent may classify a selected lodging as a hotel.  Both semantic
    # buckets are valid user-approved itinerary additions.
    allowed = set(extraction.places) | set(extraction.hotels)
    accepted = list(dict.fromkeys(payload.accepted_places))
    if any(item not in allowed for item in accepted):
        raise AppError("ATTACHMENT_SELECTION_INVALID", "确认内容不在解析预览中", 422)
    existing = {item.name for item in trip.request.must_visit}
    trip.request.must_visit.extend(
        PlaceRef(name=name) for name in accepted if name not in existing
    )
    extraction.status = "confirmed"
    state["attachment_previews"][file_id] = extraction.model_dump(mode="json")
    await trip_repo.save_planning_result(trip, state, markdown)
    return extraction


def _detect_mime(content: bytes, suffix: str) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if suffix == ".md":
        try:
            content.decode("utf-8")
            return "text/markdown"
        except UnicodeDecodeError:
            return None
    if content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
            if "word/document.xml" in names and suffix == ".docx":
                return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if "xl/workbook.xml" in names and suffix == ".xlsx":
                return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        except zipfile.BadZipFile:
            return None
    return None
