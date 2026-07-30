import json
from pathlib import Path

from app.domain.models import (
    Activity,
    DayPlan,
    MovementStage,
    PlanPatch,
    PlaceRef,
    SSEEvent,
    SkillResult,
    SourceRecord,
    Trip,
    TripRequest,
    VehicleProfile,
    VerificationIssue,
)

MODELS = [
    TripRequest,
    VehicleProfile,
    PlaceRef,
    DayPlan,
    MovementStage,
    Activity,
    PlanPatch,
    VerificationIssue,
    SourceRecord,
    SkillResult,
    SSEEvent,
    Trip,
]

target = Path(__file__).resolve().parents[2] / "shared" / "schemas"
target.mkdir(parents=True, exist_ok=True)
for model in MODELS:
    path = target / f"{model.__name__}.schema.json"
    path.write_text(
        json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
print(f"Exported {len(MODELS)} schemas to {target}")
