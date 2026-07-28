from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from workflow_runner.errors import WorkflowError, WorkflowErrorCode
from workflow_runner.models import DependencyCall


class EnterpriseApiClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_seconds: float = 10.0,
        correlation_id: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "X-Correlation-ID": self.correlation_id,
            "Accept": "application/json",
        }

    def _classify_http_error(self, response: httpx.Response) -> WorkflowError:
        if response.status_code in (401, 403):
            return WorkflowError(
                WorkflowErrorCode.AUTH_FAILURE,
                f"Authentication failed with status {response.status_code}",
                self.correlation_id,
            )
        if response.status_code == 404:
            return WorkflowError(
                WorkflowErrorCode.NOT_FOUND,
                "Requested resource was not found",
                self.correlation_id,
            )
        if response.status_code >= 500:
            return WorkflowError(
                WorkflowErrorCode.UPSTREAM_5XX,
                f"Upstream returned {response.status_code}",
                self.correlation_id,
            )
        return WorkflowError(
            WorkflowErrorCode.UNKNOWN,
            f"Unexpected status {response.status_code}",
            self.correlation_id,
        )

    def _request(self, method: str, path: str, name: str) -> tuple[dict[str, Any], DependencyCall]:
        started = time.perf_counter()
        call = DependencyCall(
            name=name,
            method=method,
            path=path,
            elapsed_ms=0.0,
            correlation_id=self.correlation_id,
        )
        try:
            client_kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            with httpx.Client(**client_kwargs) as client:
                response = client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self._headers(),
                )
        except httpx.TimeoutException as exc:
            call.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            call.error = "timeout"
            raise WorkflowError(
                WorkflowErrorCode.TIMEOUT,
                f"{name} timed out after {self.timeout_seconds}s",
                self.correlation_id,
            ) from exc
        except httpx.HTTPError as exc:
            call.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            call.error = str(exc)
            raise WorkflowError(
                WorkflowErrorCode.UNKNOWN,
                f"{name} request failed: {exc}",
                self.correlation_id,
            ) from exc

        call.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        call.status_code = response.status_code
        response_correlation = response.headers.get("X-Correlation-ID")
        if response_correlation:
            call.correlation_id = response_correlation
            self.correlation_id = response_correlation

        if response.status_code >= 400:
            call.error = response.text
            raise self._classify_http_error(response)

        try:
            payload = response.json()
        except ValueError as exc:
            call.error = "invalid_json"
            raise WorkflowError(
                WorkflowErrorCode.MALFORMED_RESPONSE,
                f"{name} returned non-JSON payload",
                self.correlation_id,
            ) from exc

        if not isinstance(payload, dict):
            call.error = "unexpected_shape"
            raise WorkflowError(
                WorkflowErrorCode.MALFORMED_RESPONSE,
                f"{name} returned unexpected JSON shape",
                self.correlation_id,
            )

        return payload, call

    def get_incident(self, incident_id: str) -> tuple[dict[str, Any], DependencyCall]:
        return self._request("GET", f"/v1/incidents/{incident_id}", "get_incident")

    def get_runbook(self, incident_id: str) -> tuple[dict[str, Any], DependencyCall]:
        return self._request("GET", f"/v1/incidents/{incident_id}/runbook", "get_runbook")
