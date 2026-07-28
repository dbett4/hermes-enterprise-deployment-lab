from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from workflow_runner.client import EnterpriseApiClient
from workflow_runner.planner import run_incident_intake


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run incident intake workflow against enterprise API")
    parser.add_argument("--incident-id", default=os.getenv("DEFAULT_INCIDENT_ID", "INC-2026-0042"))
    parser.add_argument("--api-url", default=os.getenv("ENTERPRISE_API_URL", "http://localhost:8080"))
    parser.add_argument("--token", default=os.getenv("ENTERPRISE_API_TOKEN", "lab-read-token"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("WORKFLOW_TIMEOUT_SECONDS", "10")))
    parser.add_argument(
        "--output",
        default=os.getenv("WORKFLOW_RECEIPT_PATH", "/tmp/workflow-receipt.json"),
        help="Path to write JSON receipt",
    )
    args = parser.parse_args(argv)

    client = EnterpriseApiClient(
        base_url=args.api_url,
        token=args.token,
        timeout_seconds=args.timeout,
    )
    receipt = run_incident_intake(client, args.incident_id)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(receipt.model_dump_json(indent=2))

    print(json.dumps({"outcome": receipt.outcome, "receipt_path": str(output_path)}, indent=2))

    return 0 if receipt.outcome == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
