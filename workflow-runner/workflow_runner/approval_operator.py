"""Operator-facing command for approving a pending mutation request."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from workflow_runner.approvals import ApprovalGrant, ApprovalStore
from workflow_runner.audit import AuditLog


def approve_request(
    approval_id: str,
    approver_identity: str,
    *,
    approvals: ApprovalStore | None = None,
    audit: AuditLog | None = None,
) -> tuple[ApprovalGrant | None, str | None]:
    approvals = approvals or ApprovalStore()
    audit = audit or AuditLog()
    grant, rejection = approvals.approve(approval_id, approver_identity)
    request = approvals.get(approval_id)
    if grant is None:
        audit.append(
            "approval_rejected",
            actor=approver_identity or "approval-operator",
            correlation_id=request.correlation_id if request else None,
            approval_id=approval_id,
            incident_id=request.incident_id if request else None,
            action_id=request.action_id if request else None,
            outcome=rejection or "approval_failed",
        )
        return None, rejection

    audit.append(
        "approval_granted",
        actor=grant.approved_by,
        correlation_id=request.correlation_id if request else None,
        approval_id=grant.approval_id,
        incident_id=grant.incident_id,
        action_id=grant.action_id,
        outcome="approved",
        expires_at=grant.expires_at,
    )
    return grant, None


def _result(grant: ApprovalGrant | None, rejection: str | None) -> dict[str, Any]:
    if grant is None:
        return {"status": "approval_rejected", "reason": rejection}
    return {"status": "approved", **grant.to_dict()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Grant a pending lab mutation from the separate operator channel"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    approve = subparsers.add_parser("approve", help="Approve one pending approval ID")
    approve.add_argument("approval_id")
    approve.add_argument("--approver", required=True, help="Identity recorded as the approver")
    args = parser.parse_args(argv)

    grant, rejection = approve_request(args.approval_id, args.approver)
    print(json.dumps(_result(grant, rejection), sort_keys=True))
    return 0 if grant is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
