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
        return self._send("POST", path, payload)

    def _patch(self, path: str, payload: dict) -> dict:
        """PATCH JSON to a path under the API base; re-auth on 401 and retry once."""
        return self._send("PATCH", path, payload)

    def _delete(self, path: str) -> dict:
        """DELETE a path under the API base; re-auth on 401 and retry once."""
        return self._send("DELETE", path, None)

    def _send(self, method: str, path: str, payload: dict | None) -> dict:
        """Send a write request; re-auth on 401 and retry once."""
        url = f"{self._api_base}{path}"
        resp = requests.request(method, url, headers=self._headers(), json=payload, timeout=60)
        if resp.status_code == 401:
            self._reauth()
            url = f"{self._api_base}{path}"
            resp = requests.request(method, url, headers=self._headers(), json=payload, timeout=60)
        if resp.status_code >= 400:
            raise SalesforceError(
                _api_error_message(resp),
                status_code=resp.status_code,
                body=resp.text,
            )
        if resp.status_code == 204 or not (resp.text or "").strip():
            return {"success": True}
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

    def describe_report(self, report_id: str) -> dict:
        """Fetch a report's reportMetadata (GET /analytics/reports/<id>/describe)."""
        rid = _valid_id(report_id, "report_id")
        return self._get(f"/analytics/reports/{rid}/describe").get("reportMetadata", {})

    def get_dashboard(self, dashboard_id: str) -> dict:
        """Fetch a dashboard's full saveable representation (GET .../dashboards/<id>/describe)."""
        did = _valid_id(dashboard_id, "dashboard_id")
        return self._get(f"/analytics/dashboards/{did}/describe")

    def create_dashboard(self, dashboard_metadata: dict) -> dict:
        """
        Create a dashboard via the Analytics REST API (POST /analytics/dashboards).

        The payload is validated and normalized against each referenced report's describe
        before posting (see _prepare_dashboard); Salesforce's parser silently drops unknown
        fields, so strictness here is what makes a create land with its components intact.
        """
        payload = self._prepare_dashboard(dashboard_metadata, for_update=False)
        if not payload.get("folderId"):
            payload["folderId"] = self._default_folder_id(DEFAULT_DASHBOARD_FOLDER, "Dashboard")
        result = self._post("/analytics/dashboards", payload)
        created = len(result.get("components", []))
        wanted = len(payload["components"])
        if created != wanted:
            result["warning"] = (
                f"Dashboard was created but only {created}/{wanted} components persisted. "
                "Use get_dashboard to inspect and update_dashboard to fix."
            )
        return result

    def update_dashboard(self, dashboard_id: str, dashboard_metadata: dict) -> dict:
        """
        Update a dashboard via PATCH /analytics/dashboards/<id>.

        Same validation as create_dashboard. If components are included they REPLACE the
        dashboard's entire component set (and layout must be sent alongside them).
        """
        did = _valid_id(dashboard_id, "dashboard_id")
        payload = self._prepare_dashboard(dashboard_metadata, for_update=True)
        return self._patch(f"/analytics/dashboards/{did}", payload)

    def delete_dashboard(self, dashboard_id: str) -> dict:
        """Delete a dashboard (DELETE /analytics/dashboards/<id>)."""
        did = _valid_id(dashboard_id, "dashboard_id")
        return self._delete(f"/analytics/dashboards/{did}")

    # Dashboard payload shape (verified empirically against v62.0; the docs' shapes with
    # top-level "gridLayout" or component-level "componentData" objects are parsed as unknown
    # fields and silently ignored, which yields an empty-shell dashboard).
    _DASHBOARD_KEYS = {
        "name", "description", "folderId", "developerName", "components", "layout",
        "runningUser", "dashboardType", "chartTheme", "colorPalette", "filters",
    }
    _COMPONENT_KEYS = {"type", "reportId", "header", "footer", "title", "properties"}
    _PROPERTY_KEYS = {
        "visualizationType", "aggregates", "groupings", "reportFormat", "maxRows", "sort",
        "autoSelectColumns", "useReportChart", "drillUrl", "filterColumns",
        "visualizationProperties",
    }
    # Chart types that render grouped data and therefore require groupings.
    _GROUPED_CHARTS = {"Bar", "Column", "Line", "Donut", "Pie", "Funnel", "Scatter"}
    _VIZ_TYPES = _GROUPED_CHARTS | {"FlexTable", "Table", "Gauge", "Metric"}

    def _prepare_dashboard(self, md: dict, for_update: bool) -> dict:
        """Validate and normalize a dashboard payload; raises with actionable messages."""
        if not isinstance(md, dict) or not md:
            raise SalesforceError("dashboard_metadata is required and must be a non-empty object.")
        md = dict(md)
        if "gridLayout" in md:
            raise SalesforceError(
                'Unknown key "gridLayout". The layout goes under "layout": '
                '{"gridLayout": true, "numColumns": 12, "rowHeight": 36, '
                '"components": [{"column", "row", "colspan", "rowspan"}, ...]} '
                "with one positional entry per component, matched by index."
            )
        unknown = sorted(set(md) - self._DASHBOARD_KEYS)
        if unknown:
            raise SalesforceError(
                f"Unknown dashboard key(s) {unknown} (Salesforce would silently ignore them). "
                f"Valid keys: {sorted(self._DASHBOARD_KEYS)}."
            )
        if not for_update and not md.get("name"):
            raise SalesforceError("dashboard_metadata.name is required.")

        components = md.get("components")
        if components is None and for_update:
            return md  # metadata-only update (e.g. rename)
        if not isinstance(components, list) or not components:
            raise SalesforceError(
                "dashboard_metadata.components must be a non-empty list -- a dashboard "
                "without components is an empty shell. Each component references an "
                "existing report Id (create the reports first with create_report)."
            )
        report_meta: dict[str, dict] = {}
        md["components"] = [
            self._prepare_component(comp, i, report_meta) for i, comp in enumerate(components)
        ]
        md["layout"] = self._prepare_layout(md.get("layout"), len(components))
        return md

    def _prepare_component(self, comp: dict, index: int, report_meta: dict) -> dict:
        """Validate one dashboard component against its report's describe."""
        where = f"components[{index}]"
        if not isinstance(comp, dict):
            raise SalesforceError(f"{where} must be an object.")
        comp = dict(comp)
        unknown = sorted(set(comp) - self._COMPONENT_KEYS)
        if unknown:
            raise SalesforceError(
                f"Unknown key(s) {unknown} in {where} (Salesforce would silently ignore "
                f"them). Valid keys: {sorted(self._COMPONENT_KEYS)}. Chart/table settings "
                'go under "properties".'
            )
        comp.setdefault("type", "Report")
        report_id = comp.get("reportId")
        if not report_id:
            raise SalesforceError(f"{where}.reportId is required (an existing report Id).")
        if report_id not in report_meta:
            try:
                report_meta[report_id] = self.describe_report(report_id)
            except SalesforceError as e:
                if e.status_code in (400, 404):
                    raise SalesforceError(
                        f"{where}.reportId '{report_id}' is not a valid report Id. "
                        "Use the Id returned by create_report, or run_soql on the Report object."
                    ) from e
                raise
        rm = report_meta[report_id]
        props = dict(comp.get("properties") or {})
        unknown = sorted(set(props) - self._PROPERTY_KEYS)
        if unknown:
            raise SalesforceError(
                f"Unknown key(s) {unknown} in {where}.properties. "
                f"Valid keys: {sorted(self._PROPERTY_KEYS)}."
            )
        viz = props.get("visualizationType")
        if viz not in self._VIZ_TYPES:
            raise SalesforceError(
                f"{where}.properties.visualizationType is required and must be one of "
                f"{sorted(self._VIZ_TYPES)} (got {viz!r})."
            )
        if viz == "FlexTable":
            self._prepare_flex_table(props, rm, where)
        else:
            self._prepare_chart(props, rm, viz, where)
        comp["properties"] = props
        return comp

    @staticmethod
    def _report_groupings(rm: dict) -> list:
        return [
            g.get("name")
            for g in list(rm.get("groupingsDown", [])) + list(rm.get("groupingsAcross", []))
            if isinstance(g, dict)
        ]

    def _prepare_chart(self, props: dict, rm: dict, viz: str, where: str) -> None:
        """Normalize/validate aggregates and groupings for a chart-like component."""
        report_aggs = list(rm.get("aggregates", []))
        aggs = props.get("aggregates")
        if aggs is None:
            aggs = report_aggs[:1] or ["RowCount"]
        props["aggregates"] = norm = [
            {"name": a} if isinstance(a, str) else a for a in aggs
        ]
        valid_aggs = set(report_aggs) | {"RowCount"}
        bad = [a.get("name") for a in norm if a.get("name") not in valid_aggs]
        if bad:
            raise SalesforceError(
                f"Invalid aggregate(s) {bad} in {where}: the report only has "
                f"{sorted(valid_aggs)}. Component aggregates must exist on the report."
            )
        valid_groupings = self._report_groupings(rm)
        if viz in self._GROUPED_CHARTS:
            if not valid_groupings:
                raise SalesforceError(
                    f"{where}: a {viz} chart needs a grouped (SUMMARY/MATRIX) report, but "
                    f"report '{rm.get('name')}' is {rm.get('reportFormat')} with no "
                    "groupings. Use visualizationType FlexTable, or group the report."
                )
            groupings = props.get("groupings")
            if not groupings:
                groupings = [{"name": g, "sortOrder": "Asc"} for g in valid_groupings]
            groupings = [{"name": g} if isinstance(g, str) else g for g in groupings]
            bad = [g.get("name") for g in groupings if g.get("name") not in valid_groupings]
            if bad:
                raise SalesforceError(
                    f"Invalid grouping(s) {bad} in {where}: component groupings must be "
                    f"groupings of the report. Report groupings: {valid_groupings}."
                )
            props["groupings"] = groupings

    @staticmethod
    def _prepare_flex_table(props: dict, rm: dict, where: str) -> None:
        """Normalize/validate a FlexTable component (detail columns only)."""
        # FlexTable can't mix detail columns with groupings/aggregates in tableColumns,
        # and empty tableColumns is rejected -- so default to the report's detailColumns.
        props["aggregates"] = []
        props["groupings"] = []
        vp = dict(props.get("visualizationProperties") or {})
        vp.setdefault("flexTableType", "tabular")
        columns = vp.get("tableColumns") or [
            {"column": c, "type": "detail"} for c in rm.get("detailColumns", [])
        ]
        columns = [
            {"column": c, "type": "detail"} if isinstance(c, str) else dict(c)
            for c in columns
        ]
        valid = set(rm.get("detailColumns", []))
        for c in columns:
            c.setdefault("type", "detail")
        bad = [c.get("column") for c in columns if c.get("column") not in valid]
        if bad:
            raise SalesforceError(
                f"Invalid tableColumns {bad} in {where}: FlexTable columns must be detail "
                f"columns of the report. Report detailColumns: {sorted(valid)}."
            )
        if not columns:
            raise SalesforceError(
                f"{where}: FlexTable needs tableColumns and the report has no "
                "detailColumns to default to."
            )
        vp["tableColumns"] = columns
        props["visualizationProperties"] = vp

    @staticmethod
    def _prepare_layout(layout: dict | None, n_components: int) -> dict:
        """Validate the layout, or generate a default two-across grid."""
        if layout is None:
            positions = []
            for i in range(n_components):
                last_full_width = i == n_components - 1 and n_components % 2 == 1
                positions.append({
                    "column": 0 if last_full_width else (i % 2) * 6,
                    "row": (i // 2) * 12,
                    "colspan": 12 if last_full_width else 6,
                    "rowspan": 12,
                })
            return {
                "gridLayout": True,
                "numColumns": 12,
                "rowHeight": 36,
                "components": positions,
            }
        if not isinstance(layout, dict):
            raise SalesforceError("layout must be an object (or omitted for an auto layout).")
        layout = dict(layout)
        layout.setdefault("gridLayout", True)
        layout.setdefault("numColumns", 12)
        layout.setdefault("rowHeight", 36)
        positions = layout.get("components")
        if not isinstance(positions, list) or len(positions) != n_components:
            raise SalesforceError(
                f"layout.components must have exactly one positional entry per dashboard "
                f"component ({n_components} needed), matched by index. Each entry: "
                '{"column": <0-11>, "row": <int>, "colspan": <1-12>, "rowspan": <int>}.'
            )
        for i, pos in enumerate(positions):
            missing = [k for k in ("column", "row", "colspan", "rowspan") if k not in (pos or {})]
            if missing:
                raise SalesforceError(f"layout.components[{i}] is missing {missing}.")
        return layout

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


def _valid_id(value: str, what: str) -> str:
    """Validate a 15/18-char Salesforce Id and return it stripped."""
    v = (value or "").strip()
    if not re.match(r"^[a-zA-Z0-9]{15}([a-zA-Z0-9]{3})?$", v):
        raise SalesforceError(f"{what} must be a 15- or 18-character Salesforce Id, got {value!r}.")
    return v


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
