from __future__ import annotations

from enum import Enum


class WorkflowErrorCode(str, Enum):
    AUTH_FAILURE = "auth_failure"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    MALFORMED_RESPONSE = "malformed_response"
    UPSTREAM_5XX = "upstream_5xx"
    UNKNOWN = "unknown"


class WorkflowError(Exception):
    def __init__(self, code: WorkflowErrorCode, message: str, correlation_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.correlation_id = correlation_id
