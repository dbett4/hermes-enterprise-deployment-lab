import os

# The service refuses to start without both tokens; tests use the documented fixture values.
os.environ.setdefault("ENTERPRISE_API_TOKEN", "lab-read-token")
os.environ.setdefault("ENTERPRISE_API_WRITE_TOKEN", "lab-write-token")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
