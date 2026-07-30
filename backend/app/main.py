from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.skills import router as skills_router
from .api.trips import router as trips_router
from .core.config import get_settings
from .core.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from .db import create_tables
from .skills.amap import AmapDrivingAdapter, AmapGeocodeAdapter
from .skills.registry import SkillRegistry

settings = get_settings()
logger = structlog.get_logger()
registry = SkillRegistry()
registry.register(AmapGeocodeAdapter(settings.amap_webservice_key))
registry.register(AmapDrivingAdapter(settings.amap_webservice_key))


@asynccontextmanager
async def lifespan(_: FastAPI):
    await create_tables()
    logger.info("roadman_started", environment=settings.app_env)
    yield


app = FastAPI(
    title="RoadMan API",
    version="0.1.0",
    description="RoadMan 第一阶段 Trip、SSE 与 Skill Registry API",
    lifespan=lifespan,
)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(Exception, unexpected_error_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(trips_router)
app.include_router(skills_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "roadman-api",
        "environment": settings.app_env,
        "skills": registry.names(),
    }
