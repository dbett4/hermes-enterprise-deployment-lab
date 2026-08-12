from __future__ import annotations

import asyncio
import logging
from typing import Any

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.auth import require_read_token, require_write_token
from app.config import settings
from app.fixtures import INCIDENTS, RUNBOOKS
from app.middleware import CorrelationAndLoggingMiddleware
from app.metrics import observe_action_outcome, render_metrics
from app.store import ACTION_STORE
from app.tracing import configure_tracing, shutdown_tracing

logging.basicConfig(level=logging.INFO, format="%(message)s")

_ready = False

# Failure-injection modes. `error_after_commit` is the interesting one: the side
# effect IS persisted and the caller still sees a 5xx, which is exactly the
# situation where a naive retry duplicates work.
INJECT_MODES = {"error", "error_after_commit", "timeout"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ready
    configure_tracing()
    _ready = True
    yield
    _ready = False
    shutdown_tracing()


app = FastAPI(title="Enterprise Operations API", version="0.2.0", lifespan=lifespan)
app.add_middleware(CorrelationAndLoggingMiddleware)


class ApplyActionRequest(BaseModel):
    action_id: str = Field(min_length=1)
    note: str | None = None


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


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


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


@app.post("/v1/incidents/{incident_id}/actions", status_code=status.HTTP_201_CREATED)
async def apply_incident_action(
    incident_id: str,
    payload: ApplyActionRequest,
    request: Request,
    response: Response,
    inject: str | None = Query(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _: None = Depends(require_write_token),
) -> dict[str, Any]:
    """Apply a runbook action. Write scope + idempotency key are both required."""
    if incident_id not in INCIDENTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header is required for mutations",
        )
    idempotency_key = idempotency_key.strip()

    mode = _injection_mode(request, inject)

    # Pre-commit fault: nothing is persisted.
    if mode == "timeout":
        await asyncio.sleep(settings.inject_timeout_seconds)
    if mode == "error":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Injected upstream failure before commit",
        )

    correlation_id = getattr(request.state, "correlation_id", "unknown")
    record, created = ACTION_STORE.commit(
        incident_id=incident_id,
        action_id=payload.action_id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        note=payload.note,
    )

    # Post-commit fault: the side effect exists but the caller sees a 5xx.
    if mode == "error_after_commit":
        observe_action_outcome("postcommit_error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Injected upstream failure after commit",
        )

    if not created:
        response.status_code = status.HTTP_200_OK
        observe_action_outcome("replayed")
    else:
        observe_action_outcome("created")

    return {
        "incident_id": incident_id,
        "replayed": not created,
        "record": record,
        "total_actions_for_incident": len(ACTION_STORE.list_for_incident(incident_id)),
    }


@app.get("/v1/incidents/{incident_id}/actions")
async def list_incident_actions(
    incident_id: str,
    _: None = Depends(require_read_token),
) -> dict[str, Any]:
    records = ACTION_STORE.list_for_incident(incident_id)
    return {"incident_id": incident_id, "count": len(records), "applied_actions": records}


@app.post("/v1/admin/reset-actions")
async def reset_actions(_: None = Depends(require_write_token)) -> dict[str, Any]:
    """Fixture-lab affordance so demos and tests start from a known-empty store."""
    cleared = ACTION_STORE.reset()
    return {"status": "reset", "cleared": cleared}
