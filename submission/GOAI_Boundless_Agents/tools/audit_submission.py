from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
MD = next(ROOT.glob("*.md"))
PDF = next(ROOT.glob("*.pdf"))
DOCX = next(ROOT.glob("RoadMan_*.docx"))
REVIEW = ROOT / "review"
REVIEW.mkdir(exist_ok=True)

markdown = MD.read_text(encoding="utf-8")
pdf = fitz.open(PDF)

intro_match = re.search(r"# 作品简介（500 字以内）\s+(.*?)(?=\n# )", markdown, re.S)
intro = intro_match.group(1) if intro_match else ""
intro = re.sub(r">.*", "", intro)
intro = re.sub(r"[*_`#|]", "", intro)
intro = re.sub(r"\s+", "", intro)

required_sections = [
    "场景来源、目标用户与核心问题",
    "完整任务闭环与产品交互",
    "多智能体设计",
    "工具调用、知识增强与来源追溯",
    "验证、评测与运行证据",
    "安全、合规与使用边界",
    "开放复用价值与落地计划",
    "赛道评审维度对照",
]
missing_sections = [item for item in required_sections if item not in markdown]
placeholder_codes = re.findall(r"【(?:截图|视频)占位\s+([SV]\d+)", markdown)

with zipfile.ZipFile(DOCX) as archive:
    corrupt_docx_member = archive.testzip()

page_stats = []
thumbs: list[Image.Image] = []
matrix = fitz.Matrix(0.55, 0.55)
for index, page in enumerate(pdf):
    text = page.get_text().strip()
    image_count = len(page.get_images(full=True))
    page_stats.append({"page": index + 1, "text_chars": len(text), "images": image_count})
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    thumbs.append(image)
    if index + 1 in {1, 2, 6, 10, 15, 23, pdf.page_count}:
        detail_pix = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
        detail = Image.frombytes("RGB", (detail_pix.width, detail_pix.height), detail_pix.samples)
        detail.save(REVIEW / f"page-{index + 1:02d}-detail.png")

cols, rows = 3, 3
pad, label_h = 18, 28
for sheet_index in range(0, len(thumbs), cols * rows):
    batch = thumbs[sheet_index : sheet_index + cols * rows]
    w = max(img.width for img in batch)
    h = max(img.height for img in batch)
    canvas = Image.new("RGB", (cols * w + (cols + 1) * pad, rows * (h + label_h) + (rows + 1) * pad), "#dfe9f5")
    draw = ImageDraw.Draw(canvas)
    for offset, img in enumerate(batch):
        row, col = divmod(offset, cols)
        x = pad + col * (w + pad)
        y = pad + row * (h + label_h + pad)
        canvas.paste(img, (x, y + label_h))
        draw.text((x, y + 4), f"Page {sheet_index + offset + 1}", fill="#16355f")
    canvas.save(REVIEW / f"contact-sheet-{sheet_index // (cols * rows) + 1}.png")

report = {
    "markdown": str(MD),
    "pdf": str(PDF),
    "docx": str(DOCX),
    "pdf_pages": pdf.page_count,
    "pdf_bytes": PDF.stat().st_size,
    "docx_bytes": DOCX.stat().st_size,
    "intro_compact_chars": len(intro),
    "required_sections_missing": missing_sections,
    "placeholder_codes": placeholder_codes,
    "placeholder_count": len(placeholder_codes),
    "docx_zip_corrupt_member": corrupt_docx_member,
    "very_sparse_pages": [p for p in page_stats if p["text_chars"] < 80 and p["images"] == 0],
    "page_stats": page_stats,
}
(REVIEW / "audit-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in report.items() if k != "page_stats"}, ensure_ascii=False, indent=2))
