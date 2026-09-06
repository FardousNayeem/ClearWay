"""One error envelope for the whole API.

Every failure a client can see is shaped like::

    {"error": {"code": "...", "message": "...", "details": {...}, "request_id": "..."}}

so the frontend has exactly one shape to handle.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging import current_request_id


class ClearwayError(Exception):
    """Base class for failures the caller is allowed to see."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "error"
    message = "The request could not be completed."

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details


class NotFoundError(ClearwayError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "That resource does not exist."


class ValidationError(ClearwayError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"
    message = "Some parameters need attention."


class NoDataError(ClearwayError):
    """Asked a reasonable question, but there is nothing to answer it with."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "no_data"
    message = "No air quality data covers that location yet."


class UpstreamError(ClearwayError):
    """An open data provider failed us. Never the caller's fault."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "upstream_unavailable"
    message = "An upstream data provider is not responding. Try again shortly."


class ModelUnavailableError(ClearwayError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "model_unavailable"
    message = "No trained model is available yet. Run a training job first."


def _envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": current_request_id(),
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ClearwayError)
    async def _clearway(_: Request, exc: ClearwayError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = {
            ".".join(str(part) for part in error["loc"][1:]): error["msg"]
            for error in exc.errors()
        }
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_envelope("validation_error", "Some parameters need attention.", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed", 429: "rate_limited"}
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(codes.get(exc.status_code, "error"), str(exc.detail)),
        )
