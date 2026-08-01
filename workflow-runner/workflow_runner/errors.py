from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from workflow_runner.models import DependencyCall


class WorkflowErrorCode(str, Enum):
    AUTH_FAILURE = "auth_failure"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    MALFORMED_RESPONSE = "malformed_response"
    UPSTREAM_5XX = "upstream_5xx"
    BAD_REQUEST = "bad_request"
    UNKNOWN = "unknown"


class WorkflowError(Exception):
    def __init__(
        self,
        code: WorkflowErrorCode,
        message: str,
        correlation_id: str | None = None,
        call: "DependencyCall | None" = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.correlation_id = correlation_id
        # Dependency-call evidence for the request that failed. Carrying it on the
        # exception lets callers audit failed attempts, not just successful ones.
        self.call = call
