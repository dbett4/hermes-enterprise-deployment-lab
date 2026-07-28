from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class McpSettings:
    api_url: str
    api_token: str
    timeout_seconds: float


def load_settings() -> McpSettings:
    return McpSettings(
        api_url=os.environ.get("ENTERPRISE_API_URL", "http://127.0.0.1:8080").rstrip("/"),
        api_token=os.environ.get("ENTERPRISE_API_TOKEN", "lab-read-token"),
        timeout_seconds=float(os.environ.get("ENTERPRISE_API_TIMEOUT_SECONDS", "10")),
    )
