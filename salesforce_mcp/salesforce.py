"""Salesforce REST client: token storage, refresh, and read-only API methods."""

import json
import os
import re
from pathlib import Path

import requests

# Project root (parent of salesforce_mcp package)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Default token file: <project_root>/tokens.json
# Override with SALESFORCE_MCP_TOKEN_FILE env var (relative paths resolve from project root)
DEFAULT_TOKEN_FILE = _PROJECT_ROOT / "tokens.json"
API_VERSION = "v59.0"

# DML keywords that must not appear in SOQL (read-only)
_FORBIDDEN_SOQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|UPSERT|EXECUTE)\b",
    re.IGNORECASE,
)


def _token_path() -> Path:
    path = os.environ.get("SALESFORCE_MCP_TOKEN_FILE")
    if path:
        p = Path(path)
        # Resolve relative paths from project root
        return p if p.is_absolute() else _PROJECT_ROOT / p
    return DEFAULT_TOKEN_FILE


def load_tokens() -> dict:
    """Load token data from the configured file. Raises if missing or invalid."""
    path = _token_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Salesforce token file not found: {path}. "
            "Restart the MCP server to trigger the OAuth flow."
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key in ("instance_url", "access_token", "refresh_token"):
        if key not in data:
            raise ValueError(
                f"Token file missing required key: {key}. "
                "Delete the token file and restart the MCP server to re-authenticate."
            )
    return data


def save_tokens(data: dict) -> None:
    """Write token data to the configured file."""
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


class SalesforceError(Exception):
    """Salesforce API or validation error."""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class SalesforceClient:
    """Thin read-only Salesforce REST client using stored OAuth tokens."""

    def __init__(self) -> None:
        self._tokens = load_tokens()
        self._base = self._tokens["instance_url"].rstrip("/")
        self._api_base = f"{self._base}/services/data/{API_VERSION}"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._tokens['access_token']}",
            "Content-Type": "application/json",
        }

    def _refresh(self) -> None:
        """Refresh access token using refresh_token."""
        client_id = (
            self._tokens.get("client_id")
            or os.environ.get("SALESFORCE_CLIENT_ID", "").strip()
            or os.environ.get("SALESFORCE_CONSUMER_KEY", "").strip()
        )
        client_secret = (
            self._tokens.get("client_secret")
            or os.environ.get("SALESFORCE_CLIENT_SECRET", "").strip()
            or os.environ.get("SALESFORCE_CONSUMER_SECRET", "").strip()
        )
        if not client_id or not client_secret:
            raise SalesforceError(
                "Cannot refresh: client_id and client_secret must be in token file "
                "or set via SALESFORCE_CLIENT_ID / SALESFORCE_CLIENT_SECRET env vars"
            )
        url = f"{self._base}/services/oauth2/token"
        resp = requests.post(
            url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._tokens["refresh_token"],
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise SalesforceError(
                f"Token refresh failed: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )
        data = resp.json()
        self._tokens["access_token"] = data["access_token"]
        if "instance_url" in data:
            self._tokens["instance_url"] = data["instance_url"]
            self._base = self._tokens["instance_url"].rstrip("/")
            self._api_base = f"{self._base}/services/data/{API_VERSION}"
        save_tokens(self._tokens)

    def _get(self, path: str) -> dict:
        """GET a path under the API base; refresh on 401 and retry once."""
        url = f"{self._api_base}{path}"
        resp = requests.get(url, headers=self._headers(), timeout=60)
        if resp.status_code == 401:
            self._refresh()
            resp = requests.get(url, headers=self._headers(), timeout=60)
        if resp.status_code >= 400:
            raise SalesforceError(
                f"Salesforce API error: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )
        return resp.json()

    def run_soql(self, query: str) -> dict:
        """
        Execute a SOQL query (SELECT only). Validates that the query is read-only.
        Returns the query result (records, totalSize, done, nextRecordsUrl if any).
        """
        normalized = _normalize_soql(query)
        if not normalized.strip().upper().startswith("SELECT"):
            raise SalesforceError("Only SELECT queries are allowed. Query must start with SELECT.")
        if ";" in normalized:
            raise SalesforceError("Multiple statements (semicolons) are not allowed.")
        if _FORBIDDEN_SOQL.search(normalized):
            raise SalesforceError(
                "Query must be read-only. INSERT, UPDATE, DELETE, UPSERT, EXECUTE are not allowed."
            )
        encoded = requests.utils.quote(normalized, safe="")
        return self._get(f"/query?q={encoded}")

    def describe_sobject(self, sobject: str) -> dict:
        """Describe one sObject (standard or custom). Returns full describe result."""
        if not sobject or not sobject.strip():
            raise SalesforceError("sObject name is required.")
        # Sanitize: only allow alphanumeric and underscore
        if not re.match(r"^[A-Za-z0-9_]+$", sobject.strip()):
            raise SalesforceError("Invalid sObject name.")
        return self._get(f"/sobjects/{sobject.strip()}/describe")

    def list_objects(self) -> dict:
        """List all sObjects (Describe Global). Returns sobjects array with name, label, etc."""
        return self._get("/sobjects")


def _normalize_soql(query: str) -> str:
    """Strip comments and collapse whitespace for validation."""
    # Remove single-line comments (-- ...)
    s = re.sub(r"--[^\n]*", "", query)
    # Remove block comments (/* ... */)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    return " ".join(s.split())


def get_client() -> SalesforceClient:
    """Return a Salesforce client using the configured token file."""
    return SalesforceClient()
