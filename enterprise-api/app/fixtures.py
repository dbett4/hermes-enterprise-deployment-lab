from __future__ import annotations

from typing import Any

INCIDENTS: dict[str, dict[str, Any]] = {
    "INC-2026-0042": {
        "incident_id": "INC-2026-0042",
        "title": "Elevated API latency in payment gateway",
        "severity": "high",
        "status": "investigating",
        "affected_service": "payment-gateway",
        "summary": (
            "P95 latency for /v1/charges exceeded 2s for 12 minutes. "
            "No customer-visible errors yet; synthetic monitors are degraded."
        ),
        "created_at": "2026-07-27T14:30:00Z",
        "updated_at": "2026-07-27T14:42:00Z",
    }
}

RUNBOOKS: dict[str, dict[str, Any]] = {
    "INC-2026-0042": {
        "incident_id": "INC-2026-0042",
        "runbook_id": "RB-PAY-GATEWAY-01",
        "title": "Payment Gateway Latency Response",
        "owner_team": "platform-oncall",
        "steps": [
            {
                "step_id": "RB-PAY-GATEWAY-01-S1",
                "order": 1,
                "action": "Confirm synthetic monitor status and recent deploys",
                "approval_required": False,
            },
            {
                "step_id": "RB-PAY-GATEWAY-01-S2",
                "order": 2,
                "action": "Scale payment-gateway replicas by +1 if CPU > 75%",
                "approval_required": True,
            },
            {
                "step_id": "RB-PAY-GATEWAY-01-S3",
                "order": 3,
                "action": "Page payments SRE if latency remains above SLO for 15 minutes",
                "approval_required": True,
            },
        ],
        "references": [
            "https://internal.example/runbooks/payment-gateway-latency",
        ],
    }
}
