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

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Correlation-ID": self.correlation_id,
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _classify_http_error(
        self, response: httpx.Response, call: DependencyCall | None = None
    ) -> WorkflowError:
        if response.status_code in (401, 403):
            return WorkflowError(
                WorkflowErrorCode.AUTH_FAILURE,
                f"Authentication failed with status {response.status_code}",
                self.correlation_id,
                call,
            )
        if response.status_code == 404:
            return WorkflowError(
                WorkflowErrorCode.NOT_FOUND,
                "Requested resource was not found",
                self.correlation_id,
                call,
            )
        if response.status_code == 400:
            return WorkflowError(
                WorkflowErrorCode.BAD_REQUEST,
                "Upstream rejected the request as malformed",
                self.correlation_id,
                call,
            )
        if response.status_code >= 500:
            return WorkflowError(
                WorkflowErrorCode.UPSTREAM_5XX,
                f"Upstream returned {response.status_code}",
                self.correlation_id,
                call,
            )
        return WorkflowError(
            WorkflowErrorCode.UNKNOWN,
            f"Unexpected status {response.status_code}",
            self.correlation_id,
            call,
        )

    def _request(
        self,
        method: str,
        path: str,
        name: str,
        json_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], DependencyCall]:
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
                    headers=self._headers(extra_headers),
                    json=json_body,
                    params=params,
                )
        except httpx.TimeoutException as exc:
            call.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            call.error = "timeout"
            raise WorkflowError(
                WorkflowErrorCode.TIMEOUT,
                f"{name} timed out after {self.timeout_seconds}s",
                self.correlation_id,
                call,
            ) from exc
        except httpx.HTTPError as exc:
            call.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            call.error = str(exc)
            raise WorkflowError(
                WorkflowErrorCode.UNKNOWN,
                f"{name} request failed: {exc}",
                self.correlation_id,
                call,
            ) from exc

        call.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        call.status_code = response.status_code
        response_correlation = response.headers.get("X-Correlation-ID")
        if response_correlation:
            call.correlation_id = response_correlation
            self.correlation_id = response_correlation

        if response.status_code >= 400:
            call.error = response.text
            raise self._classify_http_error(response, call)

        try:
            payload = response.json()
        except ValueError as exc:
            call.error = "invalid_json"
            raise WorkflowError(
                WorkflowErrorCode.MALFORMED_RESPONSE,
                f"{name} returned non-JSON payload",
                self.correlation_id,
                call,
            ) from exc

        if not isinstance(payload, dict):
            call.error = "unexpected_shape"
            raise WorkflowError(
                WorkflowErrorCode.MALFORMED_RESPONSE,
                f"{name} returned unexpected JSON shape",
                self.correlation_id,
                call,
            )

        return payload, call

    def get_incident(self, incident_id: str) -> tuple[dict[str, Any], DependencyCall]:
        return self._request("GET", f"/v1/incidents/{incident_id}", "get_incident")

    def get_runbook(self, incident_id: str) -> tuple[dict[str, Any], DependencyCall]:
        return self._request("GET", f"/v1/incidents/{incident_id}/runbook", "get_runbook")

    def apply_action(
        self,
        incident_id: str,
        action_id: str,
        idempotency_key: str,
        note: str | None = None,
        inject: str | None = None,
    ) -> tuple[dict[str, Any], DependencyCall]:
        """POST a mutation carrying an idempotency key.

        The key is what makes a resume safe: on replay the API returns the original
        record with `replayed: true` instead of creating a second one.
        """
        return self._request(
            "POST",
            f"/v1/incidents/{incident_id}/actions",
            "apply_incident_action",
            json_body={"action_id": action_id, "note": note},
            extra_headers={"Idempotency-Key": idempotency_key},
            params={"inject": inject} if inject else None,
        )

    def list_actions(self, incident_id: str) -> tuple[dict[str, Any], DependencyCall]:
        return self._request("GET", f"/v1/incidents/{incident_id}/actions", "list_incident_actions")
