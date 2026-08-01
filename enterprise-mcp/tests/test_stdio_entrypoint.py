from __future__ import annotations

from unittest.mock import patch

import pytest

from enterprise_mcp.server import main, mcp


def test_main_runs_stdio_with_banner_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENTERPRISE_API_TOKEN", "lab-read-token")
    with patch.object(mcp, "run") as mock_run:
        main()
    mock_run.assert_called_once_with(transport="stdio", show_banner=False)


def test_main_fails_closed_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENTERPRISE_API_TOKEN", raising=False)
    with patch.object(mcp, "run") as mock_run:
        with pytest.raises(SystemExit) as excinfo:
            main()
    assert excinfo.value.code == 2
    mock_run.assert_not_called()
