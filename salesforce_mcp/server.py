"""MCP server: register tools and run over stdio."""

import sys

from salesforce_mcp._server import mcp
from salesforce_mcp.salesforce import _token_path


def _ensure_auth() -> None:
    """If no token file exists, log a warning but don't block server startup.

    Interactive OAuth can't work inside MCP stdio startup (Claude Desktop kills
    the process before the user can complete the browser flow).  Instead, the
    server starts normally and tools return a helpful error when tokens are missing.
    Run auth manually:  uv run --directory <project> -m salesforce_mcp.auth
    """
    if _token_path().exists():
        return
    print(
        "No Salesforce tokens found. Tools will return an auth error. "
        "Run manually:  uv run --directory . -m salesforce_mcp.auth",
        file=sys.stderr,
    )


def run() -> None:
    """Run the MCP server over stdio (for Claude Desktop)."""
    _ensure_auth()
    mcp.run()
