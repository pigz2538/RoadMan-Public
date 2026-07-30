import json
from pathlib import Path

from app.domain.models import (
    Activity,
    ClarificationAnswer,
    DayPlan,
    FileRecord,
    JobCreate,
    JobRecord,
    MovementStage,
    PlanPatch,
    PlanningSnapshot,
    PlaceRef,
    SSEEvent,
    SkillResult,
    SkillCallRecord,
    SourceRecord,
    Trip,
    TripRequest,
    VehicleProfile,
    VerificationIssue,
)

MODELS = [
    TripRequest,
    ClarificationAnswer,
    PlanningSnapshot,
    VehicleProfile,
    PlaceRef,
    DayPlan,
    FileRecord,
    JobCreate,
    JobRecord,
    MovementStage,
    Activity,
    PlanPatch,
    VerificationIssue,
    SourceRecord,
    SkillResult,
    SkillCallRecord,
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
