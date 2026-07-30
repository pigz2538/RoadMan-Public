from typing import Any

import structlog
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger()


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: Any = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(request, exc.code, exc.message, exc.details),
    )


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_error_body(
            request,
            "REQUEST_VALIDATION_ERROR",
            "请求参数校验失败",
            jsonable_encoder(exc.errors()),
        ),
    )


async def http_error_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content=_error_body(
            request,
            f"HTTP_{exc.status_code}",
            str(exc.detail) if exc.detail else "请求失败",
        ),
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled_request_error",
        request_id=_request_id(request),
        path=request.url.path,
        method=request.method,
        exception_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content=_error_body(request, "INTERNAL_SERVER_ERROR", "服务暂时不可用"),
    )


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _error_body(
    request: Request,
    code: str,
    message: str,
    details: Any = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": _request_id(request),
        }
    }
