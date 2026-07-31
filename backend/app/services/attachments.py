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

    extracted = _deterministic_extract(file_id, text)
    if settings.ollama_api_key:
        llm = await _extract_with_ollama(path, mime_type, text, settings)
        if llm:
            for field in ("places", "hotels", "dates", "order_numbers"):
                current = getattr(extracted, field)
                incoming = [str(item) for item in llm.get(field, [])]
                if field in {"places", "hotels"}:
                    incoming = [_clean_place_name(item) for item in incoming]
                setattr(
                    extracted,
                    field,
                    list(dict.fromkeys([*current, *incoming]))[:50],
                )
            if not extracted.text_preview:
                extracted.text_preview = str(llm.get("summary") or "")[:1000]
        elif mime_type.startswith("image/"):
            warnings.append("图像识别服务未返回结构化结果，请人工补充或重试")
    elif mime_type.startswith("image/"):
        warnings.append("未配置多模态模型，图片已安全保存但暂不能自动识别")
    extracted.warnings.extend(warnings)
    return extracted


def _deterministic_extract(file_id: str, text: str) -> AttachmentExtraction:
    dates = re.findall(r"\b20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?\b", text)
    hotels = [
        _clean_place_name(item)
        for item in re.findall(
        r"[\u4e00-\u9fffA-Za-z0-9·（）()]{2,30}(?:酒店|宾馆|民宿|客栈)",
        text,
        )
    ]
    order_numbers = re.findall(
        r"(?:订单号|订单编号|Order\s*(?:ID|No\.?))\s*[:：]?\s*([A-Za-z0-9-]{6,40})",
        text,
        flags=re.IGNORECASE,
    )
    place_lines = [
        line.strip(" -#\t")
        for line in text.splitlines()
        if 2 <= len(line.strip()) <= 40
        and any(word in line for word in ("景区", "公园", "博物馆", "古镇", "山", "湖"))
    ]
    return AttachmentExtraction(
        file_id=file_id,
        places=list(dict.fromkeys([*hotels, *place_lines]))[:50],
        hotels=list(dict.fromkeys(hotels))[:30],
        dates=list(dict.fromkeys(dates))[:20],
        order_numbers=list(dict.fromkeys(order_numbers))[:20],
        text_preview=text.strip()[:1000],
    )


def _clean_place_name(value: str) -> str:
    return re.sub(r"^(?:计划游览|游览|入住|住宿|前往|到达)", "", value.strip())


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
