"""Salesforce REST client: Client Credentials auth and read-only API methods."""

import re

import requests

from salesforce_mcp.auth import obtain_access_token

API_VERSION = "v62.0"

# DML keywords that must not appear in SOQL (read-only)
_FORBIDDEN_SOQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|UPSERT|EXECUTE)\b",
    re.IGNORECASE,
)


class SalesforceError(Exception):
    """Salesforce API or validation error."""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class SalesforceClient:
    """Thin read-only Salesforce REST client using Client Credentials auth."""

    def __init__(self) -> None:
        token_data = obtain_access_token()
        self._access_token = token_data["access_token"]
        self._base = token_data["instance_url"].rstrip("/")
        self._api_base = f"{self._base}/services/data/{API_VERSION}"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def _reauth(self) -> None:
        """Re-obtain an access token via Client Credentials."""
        token_data = obtain_access_token()
        self._access_token = token_data["access_token"]
        self._base = token_data["instance_url"].rstrip("/")
        self._api_base = f"{self._base}/services/data/{API_VERSION}"

    def _get(self, path: str) -> dict:
        """GET a path under the API base; re-auth on 401 and retry once."""
        url = f"{self._api_base}{path}"
        resp = requests.get(url, headers=self._headers(), timeout=60)
        if resp.status_code == 401:
            self._reauth()
            url = f"{self._api_base}{path}"
            resp = requests.get(url, headers=self._headers(), timeout=60)
        if resp.status_code >= 400:
            raise SalesforceError(
                f"Salesforce API error: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        """POST JSON to a path under the API base; re-auth on 401 and retry once."""
        url = f"{self._api_base}{path}"
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=60)
        if resp.status_code == 401:
            self._reauth()
            url = f"{self._api_base}{path}"
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=60)
        if resp.status_code >= 400:
            raise SalesforceError(
                f"Salesforce API error: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )
        return resp.json()

    def create_report(self, report_metadata: dict) -> dict:
        """
        Create a report via the Analytics REST API (POST /analytics/reports).

        report_metadata is the value of the "reportMetadata" key: it must include at least
        name, reportType, reportFormat, and typically detailColumns and folderId.
        """
        if not isinstance(report_metadata, dict) or not report_metadata:
            raise SalesforceError("reportMetadata is required and must be a non-empty object.")
        for required in ("name", "reportType", "reportFormat"):
            if not report_metadata.get(required):
                raise SalesforceError(f"reportMetadata.{required} is required.")
        return self._post("/analytics/reports", {"reportMetadata": report_metadata})

    def run_soql(self, query: str) -> dict:
        """Execute a SOQL query (SELECT only)."""
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
        """Describe one sObject (standard or custom)."""
        if not sobject or not sobject.strip():
            raise SalesforceError("sObject name is required.")
        if not re.match(r"^[A-Za-z0-9_]+$", sobject.strip()):
            raise SalesforceError("Invalid sObject name.")
        return self._get(f"/sobjects/{sobject.strip()}/describe")

    def list_objects(self) -> dict:
        """List all sObjects (Describe Global)."""
        return self._get("/sobjects")


def _normalize_soql(query: str) -> str:
    """Strip comments and collapse whitespace for validation."""
    s = re.sub(r"--[^\n]*", "", query)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    return " ".join(s.split())


def get_client() -> SalesforceClient:
    """Return a Salesforce client authenticated via Client Credentials."""
    return SalesforceClient()
