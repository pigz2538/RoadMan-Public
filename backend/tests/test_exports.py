from datetime import date

from app.domain.models import PlaceRef, Trip, TripRequest
from app.services.exports import render_long_image, render_pdf, render_pptx, export_lines


def test_all_exporters_render_one_frozen_snapshot():
    trip = Trip(
        title="武汉—庐山行程",
        request=TripRequest(raw_text="武汉到庐山"),
        origin=PlaceRef(name="武汉"),
        destination=PlaceRef(name="庐山"),
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 2),
    )
    lines = export_lines(trip, "# 武汉—庐山\n\n## 第 1 天")
    assert render_pdf(lines).startswith(b"%PDF")
    assert render_pptx(lines).startswith(b"PK")
    assert render_long_image(lines).startswith(b"\x89PNG")
