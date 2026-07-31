from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.util import Inches, Pt
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from ..domain.models import Trip


def export_lines(trip: Trip, markdown: str) -> list[str]:
    """Build one deterministic snapshot used by every export format."""
    lines = [
        trip.title,
        f"日期：{trip.start_date or '待定'} 至 {trip.end_date or '待定'}",
        f"路线：{trip.origin.name if trip.origin else '待定'} → {trip.destination.name if trip.destination else '待定'}",
        "",
    ]
    if markdown:
        lines.extend(markdown.splitlines())
    else:
        for day in trip.days:
            lines.append(f"第 {day.day_index} 天 · {day.date} · {day.title}")
            for stage in day.stages:
                lines.append(
                    f"{stage.origin.name} → {stage.destination.name} · "
                    f"{stage.planned_start:%H:%M}-{stage.planned_end:%H:%M} · "
                    f"{stage.mode} · {stage.distance_km:g} km"
                )
            for activity in day.activities:
                lines.append(
                    f"{activity.planned_start:%H:%M}-{activity.planned_end:%H:%M} "
                    f"{activity.type} · {activity.place.name}"
                )
            lines.append("")
    return lines


def render_pdf(lines: Iterable[str]) -> bytes:
    output = BytesIO()
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    document = canvas.Canvas(output, pagesize=(595, 842))
    document.setTitle("RoadMan 行程安排")
    y = 800
    for index, line in enumerate(lines):
        document.setFont("STSong-Light", 18 if index == 0 else 10)
        for wrapped in _wrap(line, 28 if index == 0 else 48):
            if y < 45:
                document.showPage()
                document.setFont("STSong-Light", 10)
                y = 800
            document.drawString(42, y, wrapped)
            y -= 18 if index == 0 else 15
        if index == 0:
            y -= 8
    document.save()
    return output.getvalue()


def render_pptx(lines: Iterable[str]) -> bytes:
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    all_lines = list(lines)
    chunks = [all_lines[index:index + 28] for index in range(0, len(all_lines), 28)] or [[]]
    for slide_index, chunk in enumerate(chunks):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        title = slide.shapes.add_textbox(Inches(0.6), Inches(0.35), Inches(12), Inches(0.55))
        title.text_frame.text = chunk[0] if slide_index == 0 and chunk else f"RoadMan 行程 · 第 {slide_index + 1} 页"
        title.text_frame.paragraphs[0].font.size = Pt(26)
        title.text_frame.paragraphs[0].font.bold = True
        body = slide.shapes.add_textbox(Inches(0.75), Inches(1.1), Inches(11.8), Inches(5.9))
        body.text_frame.text = "\n".join(chunk[1:] if slide_index == 0 else chunk)
        for paragraph in body.text_frame.paragraphs:
            paragraph.font.size = Pt(13)
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def render_long_image(lines: Iterable[str]) -> bytes:
    all_lines = list(lines)
    width, line_height, padding = 1600, 34, 56
    height = max(900, padding * 2 + line_height * max(1, len(all_lines)))
    image = Image.new("RGB", (width, height), "#f4f8ff")
    draw = ImageDraw.Draw(image)
    font = _load_font(24)
    title_font = _load_font(38)
    y = padding
    for index, line in enumerate(all_lines):
        draw.text((padding, y), line[:105], fill="#10213e", font=title_font if index == 0 else font)
        y += 48 if index == 0 else line_height
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _wrap(value: str, width: int) -> list[str]:
    return [value[index:index + width] for index in range(0, len(value), width)] or [""]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/msyh.ttc"):
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()
