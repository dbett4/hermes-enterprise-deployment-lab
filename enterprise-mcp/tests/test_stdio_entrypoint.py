from __future__ import annotations

from unittest.mock import patch

from enterprise_mcp.server import main, mcp


def test_main_runs_stdio_with_banner_disabled() -> None:
    with patch.object(mcp, "run") as mock_run:
        main()
    mock_run.assert_called_once_with(transport="stdio", show_banner=False)
