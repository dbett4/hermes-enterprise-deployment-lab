from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

logger = logging.getLogger(settings.service_name)


class CorrelationAndLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        started = time.perf_counter()

        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

        response.headers["X-Correlation-ID"] = correlation_id
        logger.info(
            json.dumps(
                {
                    "service": settings.service_name,
                    "path": request.url.path,
                    "method": request.method,
                    "status": response.status_code,
                    "correlation_id": correlation_id,
                    "elapsed_ms": elapsed_ms,
                }
            )
        )
        return response
