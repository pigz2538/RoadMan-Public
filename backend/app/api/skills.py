from fastapi import APIRouter, Depends

from ..skills.base import SkillContext
from ..skills.registry import SkillRegistry

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


def get_registry() -> SkillRegistry:
    from ..main import registry

    return registry


@router.get("/health")
async def skills_health(registry: SkillRegistry = Depends(get_registry)):
    return await registry.health()


@router.post("/amap/geocode")
async def geocode(payload: dict, registry: SkillRegistry = Depends(get_registry)):
    return await registry.execute("amap.geocode", payload, SkillContext())


@router.post("/amap/driving")
async def driving(payload: dict, registry: SkillRegistry = Depends(get_registry)):
    return await registry.execute("amap.driving", payload, SkillContext())
