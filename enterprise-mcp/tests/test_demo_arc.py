from __future__ import annotations

from pathlib import Path

import pytest

from enterprise_mcp.demo import run_demo


@pytest.mark.asyncio
async def test_demo_arc_passes_against_a_live_api(clean_action_store: str, tmp_path: Path) -> None:
    """`scripts/demo.sh` runs exactly this function, so the demo cannot rot."""
    lines: list[str] = []
    result = await run_demo(
        api_url=clean_action_store,
        read_token="lab-read-token",
        write_token="lab-write-token",
        audit_path=tmp_path / "demo-audit.jsonl",
        approval_path=tmp_path / "demo-approvals.json",
        run_id="demo-arc-test",
        emit=lines.append,
    )
    assert result["demo_status"] == "passed"
    assert result["final_store_count"] == 1
    transcript = "\n".join(lines)
    assert "pending_approval" in transcript
    assert "operator command approved" in transcript
    assert "capability delivered to caller: yes (value redacted" in transcript
    assert "approval_already_applied" in transcript
    assert "replayed" in transcript
    assert "DEMO PASSED" in transcript
