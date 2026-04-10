"""
OAuth 2.0 Web Server flow for Salesforce.
Starts a local HTTP server, opens the Salesforce login URL, and on callback
exchanges the code for tokens and writes them to the token file.

Triggered automatically on first MCP server startup when tokens are missing,
or run manually: python -m salesforce_mcp.auth
"""

import argparse
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import requests

from salesforce_mcp.salesforce import save_tokens
from salesforce_mcp.salesforce import _token_path

# Default callback port; override with SALESFORCE_CALLBACK_PORT or --port
DEFAULT_PORT = 8501
# Default login host; override with SALESFORCE_LOGIN_HOST env var
DEFAULT_LOGIN_HOST = "login.salesforce.com"


def _client_id() -> str:
    # Consumer Key = Client ID (Salesforce sometimes calls these "web credentials")
    v = (
        os.environ.get("SALESFORCE_CLIENT_ID", "").strip()
        or os.environ.get("SALESFORCE_CONSUMER_KEY", "").strip()
    )
    if not v:
        raise SystemExit(
            "Set SALESFORCE_CLIENT_ID or SALESFORCE_CONSUMER_KEY "
            "(Connected App Consumer Key / web credentials)"
        )
    return v


def _client_secret() -> str:
    # Consumer Secret = Client Secret (Salesforce sometimes calls these "web credentials")
    v = (
        os.environ.get("SALESFORCE_CLIENT_SECRET", "").strip()
        or os.environ.get("SALESFORCE_CONSUMER_SECRET", "").strip()
    )
    if not v:
        raise SystemExit(
            "Set SALESFORCE_CLIENT_SECRET or SALESFORCE_CONSUMER_SECRET "
            "(Connected App Consumer Secret / web credentials)"
        )
    return v


def _callback_port(port: int | None) -> int:
    if port is not None:
        return port
    p = os.environ.get("SALESFORCE_CALLBACK_PORT", "").strip()
    return int(p) if p else DEFAULT_PORT


def _login_host() -> str:
    """Get login host from SALESFORCE_LOGIN_HOST env var, defaulting to login.salesforce.com.

    Strips any leading https:// or http:// so callers can safely prepend the scheme.
    """
    host = os.environ.get("SALESFORCE_LOGIN_HOST", "").strip() or DEFAULT_LOGIN_HOST
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host.rstrip("/")


def run_auth(port: int | None = None, output=None) -> None:
    """Run the OAuth flow: open browser, start server, handle callback, save tokens.

    Args:
        port: Override callback port (default from env or 8501).
        output: File-like object for status messages (default: sys.stdout).
                Pass sys.stderr when calling from MCP server startup to avoid
                interfering with the stdio MCP protocol.
    """
    if output is None:
        output = sys.stdout

    client_id = _client_id()
    client_secret = _client_secret()
    port = _callback_port(port)
    host = _login_host()
    redirect_uri = f"http://localhost:{port}/callback"

    auth_url = (
        f"https://{host}/services/oauth2/authorize"
        f"?response_type=code"
        f"&client_id={requests.utils.quote(client_id)}"
        f"&redirect_uri={requests.utils.quote(redirect_uri)}"
        f"&scope=api refresh_token"
    )

    result = {"code": None, "error": None}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/callback":
                qs = parse_qs(parsed.query)
                result["code"] = (qs.get("code") or [None])[0]
                result["error"] = (qs.get("error") or [None])[0]
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                if result["error"]:
                    self.wfile.write(
                        f"<body><p>Authorization failed: {result['error']}</p></body>".encode()
                    )
                else:
                    self.wfile.write(
                        b"<body><p>Authorization successful. You can close this tab.</p></body>"
                    )
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    print(f"Open this URL in your browser (callback: {redirect_uri}):", file=output)
    print(auth_url, file=output)
    webbrowser.open(auth_url)
    print("Waiting for callback...", file=output)
    server.handle_request()
    server.server_close()

    if result["error"]:
        raise SystemExit(f"OAuth error: {result['error']}")

    code = result["code"]
    if not code:
        raise SystemExit("No authorization code received. Try again.")

    token_url = f"https://{host}/services/oauth2/token"
    resp = requests.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Token exchange failed: {resp.status_code} {resp.text}")

    data = resp.json()
    token_data = {
        "instance_url": data["instance_url"],
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if not token_data["refresh_token"]:
        raise SystemExit(
            "No refresh_token in response. Ensure the Connected App has "
            "'Allow refresh token' and scope includes refresh_token."
        )

    save_tokens(token_data)
    path = _token_path()
    print(f"Tokens saved to {path}", file=output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Salesforce OAuth 2.0 one-time auth for MCP server"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Callback port (default: {DEFAULT_PORT} or SALESFORCE_CALLBACK_PORT)",
    )
    parser.add_argument(
        "--login-host",
        default=None,
        help="Salesforce login host (default: login.salesforce.com or SALESFORCE_LOGIN_HOST). "
        "Use test.salesforce.com for sandbox.",
    )
    args = parser.parse_args()
    if args.login_host:
        os.environ["SALESFORCE_LOGIN_HOST"] = args.login_host
    run_auth(port=args.port)


if __name__ == "__main__":
    main()
