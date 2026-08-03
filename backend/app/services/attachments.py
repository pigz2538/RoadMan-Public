from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx
from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader

from ..core.config import Settings
from ..domain.models import AttachmentExtraction


async def extract_attachment(
    file_id: str,
    path: Path,
    mime_type: str,
    settings: Settings,
) -> AttachmentExtraction:
    warnings: list[str] = []
    text = ""
    if mime_type == "application/pdf":
        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    elif mime_type.endswith("wordprocessingml.document"):
        text = "\n".join(paragraph.text for paragraph in Document(path).paragraphs)
    elif mime_type.endswith("spreadsheetml.sheet"):
        workbook = load_workbook(path, read_only=True, data_only=True)
        text = "\n".join(
            " | ".join(str(value) for value in row if value is not None)
            for sheet in workbook.worksheets
            for row in sheet.iter_rows(values_only=True)
        )
    elif mime_type in {"text/markdown", "text/plain"}:
        text = path.read_text(encoding="utf-8", errors="replace")

    # Attachments are semantic input. Do not infer places or dates from local
    # keyword lists: without the Agent we preserve only a safe text preview.
    extracted = AttachmentExtraction(
        file_id=file_id,
        text_preview=text.strip()[:1000],
    )
    if settings.ollama_api_key:
        llm = await _extract_with_ollama(path, mime_type, text, settings)
        if llm:
            for field in ("places", "hotels", "dates", "order_numbers"):
                incoming = [str(item) for item in llm.get(field, [])]
                if field in {"places", "hotels"}:
                    incoming = [_clean_place_name(item) for item in incoming]
                setattr(extracted, field, list(dict.fromkeys(incoming))[:50])
            if not extracted.text_preview or llm.get("summary"):
                extracted.text_preview = str(llm.get("summary") or "")[:1000]
        elif mime_type.startswith("image/"):
            warnings.append("图像识别服务未返回结构化结果，请人工补充或重试")
    else:
        warnings.append("未配置附件理解 Agent，仅保留文本预览，未进行语义猜测")
    extracted.warnings.extend(warnings)
    return extracted


def _clean_place_name(value: str) -> str:
    return " ".join(value.split())


async def _extract_with_ollama(
    path: Path,
    mime_type: str,
    text: str,
    settings: Settings,
) -> dict[str, Any] | None:
    prompt = (
        "你是 RoadMan 附件信息提取器。只提取，不规划，不猜测。"
        "返回单个 JSON：places,hotels,dates,order_numbers 均为字符串数组，summary 为短摘要。"
        "无法确认的字段返回空数组。"
    )
    payload: dict[str, Any] = {
        "model": settings.ollama_model,
        "prompt": f"{prompt}\n附件文字：{text[:12000]}",
        "stream": False,
        "think": False,
    }
    if mime_type.startswith("image/"):
        payload["images"] = [base64.b64encode(path.read_bytes()).decode("ascii")]
    try:
        async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
            response = await client.post(
                settings.ollama_api_url,
                headers={"Authorization": f"Bearer {settings.ollama_api_key}"},
                json=payload,
            )
            response.raise_for_status()
        match = re.search(r"\{.*\}", response.json().get("response", ""), re.DOTALL)
        value = json.loads(match.group(0)) if match else None
        return value if isinstance(value, dict) else None
    except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
        return None
