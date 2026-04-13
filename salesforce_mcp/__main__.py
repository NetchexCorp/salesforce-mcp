"""Entrypoint: run the MCP server."""

import os
import sys

from salesforce_mcp._server import mcp


def main() -> None:
    transport = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        from salesforce_mcp.server import run
        run()
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
