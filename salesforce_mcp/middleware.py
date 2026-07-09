"""API key authentication middleware for the MCP server.

Two access tiers, selected by which API key the client presents:

  MCP_ADMIN_API_KEY  -> "full"  (read + create/update/delete reports and dashboards)
  MCP_API_KEY        -> "read"  (read-only) when MCP_ADMIN_API_KEY is also set;
                        "full" when it is the ONLY key (backward compatible with
                        single-key deployments)

The resolved tier is stored in the ASGI scope state as "access_tier"; write tools
read it via the MCP request context and refuse to run for "read" requests.
When no keys are configured at all (local development), everything passes with
full access.
"""

import hmac
import json
import os

from starlette.types import ASGIApp, Receive, Scope, Send

TIER_FULL = "full"
TIER_READ = "read"


class ApiKeyMiddleware:
    """ASGI middleware that checks for a valid API key on protected paths."""

    PROTECTED_PATH = "/mcp"

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.read_key = os.environ.get("MCP_API_KEY", "").strip()
        self.admin_key = os.environ.get("MCP_ADMIN_API_KEY", "").strip()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith(self.PROTECTED_PATH):
            await self.app(scope, receive, send)
            return

        if not (self.read_key or self.admin_key):
            # No keys configured (local development): full access. The tier is still
            # set explicitly so the write gate can fail closed when it is absent.
            scope.setdefault("state", {})["access_tier"] = TIER_FULL
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth_value = headers.get(b"authorization", b"").decode()

        if not auth_value.startswith("Bearer "):
            await self._send_401(send, "Missing or malformed Authorization header")
            return

        token = auth_value[7:]
        tier = self._tier_for(token)
        if tier is None:
            await self._send_401(send, "Invalid API key")
            return

        scope.setdefault("state", {})["access_tier"] = tier
        await self.app(scope, receive, send)

    def _tier_for(self, token: str) -> str | None:
        """Map a presented token to an access tier, or None if invalid."""
        if self.admin_key and hmac.compare_digest(token, self.admin_key):
            return TIER_FULL
        if self.read_key and hmac.compare_digest(token, self.read_key):
            # Without a separate admin key there is only one tier: full access.
            return TIER_READ if self.admin_key else TIER_FULL
        return None

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
