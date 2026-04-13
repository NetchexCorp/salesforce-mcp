"""API key authentication middleware for the MCP server."""

import json
import os

from starlette.types import ASGIApp, Receive, Scope, Send


class ApiKeyMiddleware:
    """ASGI middleware that checks for a valid API key on protected paths."""

    PROTECTED_PATH = "/mcp"

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.api_key = os.environ.get("MCP_API_KEY", "").strip()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.api_key:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith(self.PROTECTED_PATH):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth_value = headers.get(b"authorization", b"").decode()

        if not auth_value.startswith("Bearer "):
            await self._send_401(send, "Missing or malformed Authorization header")
            return

        token = auth_value[7:]
        if token != self.api_key:
            await self._send_401(send, "Invalid API key")
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _send_401(send: Send, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
