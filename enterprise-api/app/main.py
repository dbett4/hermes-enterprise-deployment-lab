from __future__ import annotations

import asyncio
import logging
from typing import Any

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status

from app.auth import require_read_token
from app.config import settings
from app.fixtures import INCIDENTS, RUNBOOKS
from app.middleware import CorrelationAndLoggingMiddleware

logging.basicConfig(level=logging.INFO, format="%(message)s")

_ready = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ready
    _ready = True
    yield
    _ready = False


app = FastAPI(title="Enterprise Operations API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CorrelationAndLoggingMiddleware)


def _injection_mode(request: Request, inject: str | None) -> str | None:
    header_value = request.headers.get("X-Inject-Failure")
    return (inject or header_value or "").strip().lower() or None


async def _apply_failure_injection(mode: str | None) -> None:
    if mode == "timeout":
        await asyncio.sleep(settings.inject_timeout_seconds)
    if mode == "error":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Injected upstream failure",
        )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    if not _ready:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="not ready")
    return {"status": "ready"}


@app.get("/v1/incidents/{incident_id}")
async def get_incident(
    incident_id: str,
    request: Request,
    inject: str | None = Query(default=None),
    _: None = Depends(require_read_token),
) -> dict[str, Any]:
    await _apply_failure_injection(_injection_mode(request, inject))
    incident = INCIDENTS.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return incident


@app.get("/v1/incidents/{incident_id}/runbook")
async def get_runbook(
    incident_id: str,
    request: Request,
    inject: str | None = Query(default=None),
    _: None = Depends(require_read_token),
) -> dict[str, Any]:
    await _apply_failure_injection(_injection_mode(request, inject))
    runbook = RUNBOOKS.get(incident_id)
    if runbook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runbook not found")
    return runbook
