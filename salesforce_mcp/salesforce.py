"""Salesforce REST client: Client Credentials auth and read-only API methods."""

import re

import requests

from salesforce_mcp.auth import obtain_access_token

API_VERSION = "v62.0"

# Default folders (by DeveloperName) used when no folderId is given to create_report /
# create_dashboard. Create them once per org:
#   sf data create record --sobject Folder --values "Name='Claude Reports' DeveloperName='Claude_Reports' AccessType='Public' Type='Report'"
#   sf data create record --sobject Folder --values "Name='Claude Dashboards' DeveloperName='Claude_Dashboards' AccessType='Public' Type='Dashboard'"
DEFAULT_REPORT_FOLDER = "Claude_Reports"
DEFAULT_DASHBOARD_FOLDER = "Claude_Dashboards"

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
                _api_error_message(resp),
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
                _api_error_message(resp),
                status_code=resp.status_code,
                body=resp.text,
            )
        return resp.json()

    def list_report_types(self) -> list:
        """
        List available report types (GET /analytics/reportTypes), compacted to
        [{category, types: [{type, label}]}] and excluding hidden types.
        """
        raw = self._get("/analytics/reportTypes")
        result = []
        for category in raw:
            types = [
                {"type": rt["type"], "label": rt["label"]}
                for rt in category.get("reportTypes", [])
                if not rt.get("isHidden")
            ]
            if types:
                result.append({"category": category.get("label"), "types": types})
        return result

    def describe_report_type(self, report_type: str) -> dict:
        """
        Describe one report type (GET /analytics/report-types/<type>), compacted to the
        column names usable in detailColumns/groupings/filters, grouped by category.
        Picklist columns include their valid filter values (apiName); filterOperators maps
        each dataType to its valid filter operator names.
        """
        raw = self._describe_report_type_raw(report_type)
        rtm = raw.get("reportTypeMetadata", {})
        columns = {}
        for category in rtm.get("categories", []):
            cols = []
            for name, info in category.get("columns", {}).items():
                col = {"name": name, "label": info.get("label"), "dataType": info.get("dataType")}
                values = [v.get("apiName") for v in info.get("filterValues", []) if v.get("apiName")]
                if values:
                    col["filterValues"] = values
                cols.append(col)
            columns[category.get("label", "")] = cols
        operators = {
            data_type: [op.get("name") for op in ops]
            for data_type, ops in rtm.get("dataTypeFilterOperatorMap", {}).items()
        }
        return {
            "type": report_type.strip(),
            "supportsJoinedFormat": rtm.get("supportsJoinedFormat"),
            "columns": columns,
            "filterOperators": operators,
        }

    def _describe_report_type_raw(self, report_type: str) -> dict:
        """Fetch the raw report type describe."""
        if not report_type or not report_type.strip():
            raise SalesforceError("report_type is required.")
        if not re.match(r"^[A-Za-z0-9_@.$]+$", report_type.strip()):
            raise SalesforceError("Invalid report type name.")
        encoded = requests.utils.quote(report_type.strip(), safe="")
        return self._get(f"/analytics/report-types/{encoded}")

    def _default_folder_id(self, developer_name: str, folder_type: str) -> str:
        """Resolve a default folder Id by DeveloperName and Type ('Report' or 'Dashboard')."""
        query = (
            f"SELECT Id FROM Folder WHERE DeveloperName = '{developer_name}' "
            f"AND Type = '{folder_type}' LIMIT 1"
        )
        encoded = requests.utils.quote(query, safe="")
        records = self._get(f"/query?q={encoded}").get("records", [])
        if not records:
            raise SalesforceError(
                f"Default {folder_type.lower()} folder '{developer_name}' not found in the org. "
                f"Create it with: sf data create record --sobject Folder --values "
                f"\"Name='Claude {folder_type}s' DeveloperName='{developer_name}' "
                f"AccessType='Public' Type='{folder_type}'\" -- or pass an explicit folderId."
            )
        return records[0]["Id"]

    def create_report(self, report_metadata: dict) -> dict:
        """
        Create a report via the Analytics REST API (POST /analytics/reports).

        report_metadata is the value of the "reportMetadata" key: it must include at least
        name, reportType, reportFormat, and typically detailColumns. If folderId is omitted,
        the default 'Claude_Reports' folder is used.

        Before posting, the payload is validated against the report type's describe so that
        an invalid reportType or detailColumns fails fast with the valid options, instead of
        sending a bad write to Salesforce.
        """
        if not isinstance(report_metadata, dict) or not report_metadata:
            raise SalesforceError("reportMetadata is required and must be a non-empty object.")
        for required in ("name", "reportType", "reportFormat"):
            if not report_metadata.get(required):
                raise SalesforceError(f"reportMetadata.{required} is required.")
        report_format = str(report_metadata["reportFormat"]).upper()
        if report_format not in ("TABULAR", "SUMMARY", "MATRIX", "MULTI_BLOCK"):
            raise SalesforceError(
                "reportMetadata.reportFormat must be one of: TABULAR, SUMMARY, MATRIX, MULTI_BLOCK."
            )
        self._validate_report_columns(report_metadata)
        if not report_metadata.get("folderId"):
            report_metadata["folderId"] = self._default_folder_id(DEFAULT_REPORT_FOLDER, "Report")
        return self._post("/analytics/reports", {"reportMetadata": report_metadata})

    def _validate_report_columns(self, report_metadata: dict) -> None:
        """Validate reportType, columns, groupings, and filters against the type describe."""
        report_type = report_metadata["reportType"]
        type_name = report_type.get("type") if isinstance(report_type, dict) else report_type
        if not type_name:
            raise SalesforceError('reportMetadata.reportType must be {"type": "<ReportType>"}.')
        try:
            described = self.describe_report_type(str(type_name))
        except SalesforceError as e:
            if e.status_code in (400, 404):
                raise SalesforceError(
                    f"Report type '{type_name}' does not exist. "
                    "Use list_report_types to see valid types."
                ) from e
            raise
        columns = {
            col["name"]: col for cols in described["columns"].values() for col in cols
        }

        def check(names: list, where: str) -> None:
            invalid = [n for n in names if n and n not in columns]
            if invalid:
                suggestions = {}
                for name in invalid:
                    token = name.split(".")[-1].replace("_", "").lower()
                    close = [
                        v for v in columns
                        if token in v.replace("_", "").replace(".", "").lower()
                    ]
                    suggestions[name] = sorted(close)[:5]
                raise SalesforceError(
                    f"Invalid {where} for report type '{type_name}': {invalid}. "
                    f"Close matches: {suggestions}. "
                    f"Use describe_report_type('{type_name}') for all valid columns."
                )

        check(list(report_metadata.get("detailColumns", [])), "detailColumns")
        groupings = list(report_metadata.get("groupingsDown", [])) + list(
            report_metadata.get("groupingsAcross", [])
        )
        check([g.get("name") for g in groupings if isinstance(g, dict)], "grouping names")
        filters = [f for f in report_metadata.get("reportFilters", []) if isinstance(f, dict)]
        check([f.get("column") for f in filters], "reportFilters columns")
        for f in filters:
            self._check_picklist_filter(f, columns, str(type_name))

    @staticmethod
    def _check_picklist_filter(f: dict, columns: dict, type_name: str) -> None:
        """Validate a filter's value(s) against the picklist's valid apiNames."""
        col = columns.get(f.get("column"))
        valid_values = (col or {}).get("filterValues")
        if not valid_values or f.get("operator") not in ("equals", "notEqual"):
            return
        value = str(f.get("value", ""))
        # A comma-separated value means OR; a single value may itself contain a comma.
        parts = [value] if value in valid_values else [p.strip() for p in value.split(",")]
        invalid = [p for p in parts if p and p not in valid_values]
        if invalid:
            raise SalesforceError(
                f"Invalid picklist value(s) {invalid} for filter column '{f['column']}' "
                f"(report type '{type_name}'). Valid values: {valid_values}. "
                f"For multiple values (OR), pass them comma-separated in one filter, "
                f'e.g. "Net New,New Business".'
            )

    def create_dashboard(self, dashboard_metadata: dict) -> dict:
        """
        Create a dashboard via the Analytics REST API (POST /analytics/dashboards).

        dashboard_metadata is the dashboard representation sent as the request body: it must
        include at least name, and typically components (each referencing an existing report
        Id) and gridLayout. If folderId is omitted, the default 'Claude_Dashboards' folder
        is used.
        """
        if not isinstance(dashboard_metadata, dict) or not dashboard_metadata:
            raise SalesforceError("dashboardMetadata is required and must be a non-empty object.")
        if not dashboard_metadata.get("name"):
            raise SalesforceError("dashboardMetadata.name is required.")
        if not dashboard_metadata.get("folderId"):
            dashboard_metadata["folderId"] = self._default_folder_id(
                DEFAULT_DASHBOARD_FOLDER, "Dashboard"
            )
        return self._post("/analytics/dashboards", dashboard_metadata)

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


def _api_error_message(resp) -> str:
    """Build an error message that includes Salesforce's response body (truncated)."""
    body = (resp.text or "").strip()
    if len(body) > 2000:
        body = body[:2000] + "...(truncated)"
    return f"Salesforce API error {resp.status_code}: {body}"


def _normalize_soql(query: str) -> str:
    """Strip comments and collapse whitespace for validation."""
    s = re.sub(r"--[^\n]*", "", query)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    return " ".join(s.split())


def get_client() -> SalesforceClient:
    """Return a Salesforce client authenticated via Client Credentials."""
    return SalesforceClient()
