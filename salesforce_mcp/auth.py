"""
OAuth 2.0 Client Credentials flow for Salesforce.

Uses client_id + client_secret to obtain an access token directly —
no browser, no redirect, no refresh token needed.

Environment variables:
  SALESFORCE_CLIENT_ID      — Connected App Consumer Key
  SALESFORCE_CLIENT_SECRET  — Connected App Consumer Secret
  SALESFORCE_LOGIN_HOST     — login.salesforce.com (default) or test.salesforce.com
"""

import os

import requests

# Default login host; override with SALESFORCE_LOGIN_HOST env var
DEFAULT_LOGIN_HOST = "login.salesforce.com"


def _client_id() -> str:
    v = (
        os.environ.get("SALESFORCE_CLIENT_ID", "").strip()
        or os.environ.get("SALESFORCE_CONSUMER_KEY", "").strip()
    )
    if not v:
        raise RuntimeError(
            "Set SALESFORCE_CLIENT_ID (Connected App Consumer Key)"
        )
    return v


def _client_secret() -> str:
    v = (
        os.environ.get("SALESFORCE_CLIENT_SECRET", "").strip()
        or os.environ.get("SALESFORCE_CONSUMER_SECRET", "").strip()
    )
    if not v:
        raise RuntimeError(
            "Set SALESFORCE_CLIENT_SECRET (Connected App Consumer Secret)"
        )
    return v


def _login_host() -> str:
    host = os.environ.get("SALESFORCE_LOGIN_HOST", "").strip() or DEFAULT_LOGIN_HOST
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host.rstrip("/")


def obtain_access_token() -> dict:
    """Obtain an access token via the Client Credentials grant.

    Returns dict with keys: instance_url, access_token.
    Raises RuntimeError on failure.
    """
    client_id = _client_id()
    client_secret = _client_secret()
    host = _login_host()

    token_url = f"https://{host}/services/oauth2/token"
    resp = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Client Credentials token request failed ({resp.status_code}): {resp.text}"
        )

    data = resp.json()
    return {
        "instance_url": data["instance_url"],
        "access_token": data["access_token"],
    }
