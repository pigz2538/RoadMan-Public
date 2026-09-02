from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.files import router as files_router
from .api.jobs import router as jobs_router
from .api.skills import router as skills_router
from .api.trips import router as trips_router
from .api.vehicles import router as vehicles_router
from .api.versions import router as versions_router
from .api.ops import request_metrics, router as ops_router, set_request_metrics
from .core.config import get_settings
from .core.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from .db import create_tables
from .services.registry_factory import build_skill_registry
from .services.observability import RequestMetrics, SlidingWindowRateLimiter
from .planning.llm import llm_config_summary

settings = get_settings()
logger = structlog.get_logger()
registry = build_skill_registry(settings)
metrics = RequestMetrics()
set_request_metrics(metrics)
rate_limiter = SlidingWindowRateLimiter(settings.rate_limit_per_minute)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await create_tables()
    logger.info("roadman_started", environment=settings.app_env)
    yield
    await registry.close()


app = FastAPI(
    title="RoadMan API",
    version="0.3.0",
    description="RoadMan LangGraph 需求澄清、真实路线与行程安排 API",
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
    trace_id = request.headers.get("X-Trace-ID") or request_id
    request.state.request_id = request_id
    request.state.trace_id = trace_id
    client_key = request.client.host if request.client else "unknown"
    if settings.enable_rate_limit and not request.url.path.startswith("/health") and not rate_limiter.allow(client_key):
        metrics.record(request.url.path, 429, 0)
        return JSONResponse(
            status_code=429,
            content={"error": {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后重试", "details": None, "request_id": request_id}},
            headers={"Retry-After": "60", "X-Request-ID": request_id, "X-Trace-ID": trace_id},
        )
    started = perf_counter()
    try:
        response = await call_next(request)
    finally:
        elapsed = (perf_counter() - started) * 1000
        status_code = getattr(locals().get("response"), "status_code", 500)
        metrics.record(request.url.path, status_code, elapsed)
        logger.info(
            "request_finished",
            request_id=request_id,
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            latency_ms=round(elapsed, 2),
        )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Trace-ID"] = trace_id
    return response


app.include_router(trips_router)
app.include_router(skills_router)
app.include_router(vehicles_router)
app.include_router(files_router)
app.include_router(jobs_router)
app.include_router(versions_router)
app.include_router(ops_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "roadman-api",
        "environment": settings.app_env,
        "skills": registry.names(),
        "llm": llm_config_summary(settings),
    }
