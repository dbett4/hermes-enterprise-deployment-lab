from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
READ_TOKEN = "lab-read-token"
WRITE_TOKEN = "lab-write-token"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def live_api() -> str:
    """A real enterprise-api process, so stdio/subprocess proofs hit real HTTP."""
    port = _free_port()
    env = {
        **os.environ,
        "ENTERPRISE_API_TOKEN": READ_TOKEN,
        "ENTERPRISE_API_WRITE_TOKEN": WRITE_TOKEN,
        "PYTHONPATH": str(REPO_ROOT / "enterprise-api"),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO_ROOT / "enterprise-api"),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    try:
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError("enterprise-api exited during startup")
            try:
                if httpx.get(f"{base_url}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise RuntimeError("enterprise-api did not become healthy in time")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()


@pytest.fixture
def clean_action_store(live_api: str) -> str:
    httpx.post(
        f"{live_api}/v1/admin/reset-actions",
        headers={"Authorization": f"Bearer {WRITE_TOKEN}"},
        timeout=10,
    ).raise_for_status()
    return live_api
