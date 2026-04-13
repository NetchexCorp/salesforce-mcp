"""MCP server: register tools and run over Streamable HTTP."""

import asyncio
import os

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from salesforce_mcp._server import mcp
from salesforce_mcp.middleware import ApiKeyMiddleware


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


def run() -> None:
    """Run the MCP server over Streamable HTTP transport."""
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8765"))

    mcp.settings.host = host
    mcp.settings.port = port

    # Disable DNS rebinding protection for cloud deployments (API key auth handles security)
    allowed_hosts = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if allowed_hosts:
        if allowed_hosts == "*":
            mcp.settings.transport_security.enable_dns_rebinding_protection = False
        else:
            for h in allowed_hosts.split(","):
                mcp.settings.transport_security.allowed_hosts.append(h.strip())
                mcp.settings.transport_security.allowed_origins.append(f"https://{h.strip()}")

    starlette_app = mcp.streamable_http_app()
    app = ApiKeyMiddleware(starlette_app)

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    asyncio.run(server.serve())
