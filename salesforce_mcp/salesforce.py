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


# sObject key prefixes, used to resolve Ids out of pasted URLs and to catch swapped Ids.
_KEY_PREFIXES = {"report": "00O", "dashboard": "01Z"}


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
        scope_info = rtm.get("scopeInfo") or {}
        scopes = {
            "default": scope_info.get("defaultValue"),
            "values": [
                {"value": v.get("value"), "label": v.get("label")}
                for v in scope_info.get("values", [])
            ],
        }
        return {
            "type": report_type.strip(),
            "supportsJoinedFormat": rtm.get("supportsJoinedFormat"),
            "scopes": scopes,
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
        described = self._validate_report_columns(report_metadata)
        # Salesforce defaults new reports to a "my records" scope and a created-this-week
        # date window, which makes them (and any dashboard on top) look empty for everyone
        # but the author. Default to org-wide / all-time instead unless the caller chose.
        scope_values = {v["value"] for v in described.get("scopes", {}).get("values", [])}
        if "scope" not in report_metadata and "organization" in scope_values:
            report_metadata["scope"] = "organization"
        had_date_filter = "standardDateFilter" in report_metadata
        if not report_metadata.get("folderId"):
            report_metadata["folderId"] = self._default_folder_id(DEFAULT_REPORT_FOLDER, "Report")
        result = self._post("/analytics/reports", {"reportMetadata": report_metadata})
        created = result.get("reportMetadata", {})
        sdf = created.get("standardDateFilter") or {}
        if not had_date_filter and created.get("id") and (sdf.get("startDate") or sdf.get("endDate")):
            # Widen the org-imposed default date window to All Time (CUSTOM, no bounds).
            result = self._patch(
                f"/analytics/reports/{created['id']}",
                {"reportMetadata": {"standardDateFilter": {
                    "column": sdf.get("column"),
                    "durationValue": "CUSTOM",
                    "startDate": None,
                    "endDate": None,
                }}},
            )
        return result

    def _validate_report_columns(self, report_metadata: dict) -> dict:
        """
        Validate reportType, columns, groupings, and filters against the type describe.
        Returns the compact type describe for further use.
        """
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
        return described

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
        rid = _resolve_id(report_id, "report")
        return self._get(f"/analytics/reports/{rid}/describe").get("reportMetadata", {})

    def update_report(self, report_id: str, report_metadata: dict) -> dict:
        """
        Update a report via PATCH /analytics/reports/<id>.

        report_metadata may be partial: only the provided keys change (e.g. just
        {"scope": "organization"} or just standardDateFilter); everything else is kept.
        Column-bearing fields are validated against the report's type describe.
        """
        rid = _resolve_id(report_id, "report")
        if not isinstance(report_metadata, dict) or not report_metadata:
            raise SalesforceError("report_metadata is required and must be a non-empty object.")
        report_metadata = dict(report_metadata)
        if any(
            k in report_metadata
            for k in ("detailColumns", "groupingsDown", "groupingsAcross", "reportFilters")
        ):
            if "reportType" not in report_metadata:
                report_metadata["reportType"] = self.describe_report(rid).get("reportType")
            self._validate_report_columns(report_metadata)
        return self._patch(f"/analytics/reports/{rid}", {"reportMetadata": report_metadata})

    def delete_report(self, report_id: str) -> dict:
        """Delete a report (DELETE /analytics/reports/<id>)."""
        rid = _resolve_id(report_id, "report")
        return self._delete(f"/analytics/reports/{rid}")

    # Keys of reportMetadata that may be overridden for a one-off run (nothing is saved).
    _RUN_OVERRIDE_KEYS = {
        "scope", "standardDateFilter", "reportFilters", "reportBooleanFilter",
        "detailColumns", "aggregates", "groupingsDown", "groupingsAcross", "sortBy",
        "topRows", "hasDetailRows",
    }

    def run_report(
        self,
        report_id: str,
        include_details: bool = True,
        detail_row_limit: int = 100,
        metadata_overrides: dict | None = None,
    ) -> dict:
        """
        Run a report synchronously (POST /analytics/reports/<id>?includeDetails=...) and
        return a compacted result: grouped aggregate rows, capped detail rows, and warnings
        for the traps (2000-row API cap, "my records" scope under the integration user).

        metadata_overrides is a partial reportMetadata applied for THIS RUN ONLY (the saved
        report is untouched), e.g. {"scope": "organization"} to widen a "My ..." report.
        """
        rid = _resolve_id(report_id, "report")
        described = self._get(f"/analytics/reports/{rid}/describe")
        rm = dict(described.get("reportMetadata", {}))
        overrides = dict(metadata_overrides or {})
        if overrides:
            unknown = sorted(set(overrides) - self._RUN_OVERRIDE_KEYS)
            if unknown:
                raise SalesforceError(
                    f"Unknown metadata_overrides key(s) {unknown}. "
                    f"Valid keys: {sorted(self._RUN_OVERRIDE_KEYS)}."
                )
            # Replacing the filters invalidates the saved boolean filter (it refers to
            # filters by index) unless the caller supplies a matching one.
            if "reportFilters" in overrides and "reportBooleanFilter" not in overrides:
                overrides["reportBooleanFilter"] = None
            rm.update(overrides)
        details = bool(include_details)
        run = self._post(
            f"/analytics/reports/{rid}?includeDetails={'true' if details else 'false'}",
            {"reportMetadata": rm},
        )
        return _compact_run(run, detail_row_limit, overridden=sorted(overrides))

    def report_to_soql(self, report_id: str) -> dict:
        """
        Convert a report's definition into best-effort SOQL (see the tool docstring).
        Returns {baseObject, soql, aggregateSoql?, columns, unmapped, caveats}.
        """
        rid = _resolve_id(report_id, "report")
        described = self._get(f"/analytics/reports/{rid}/describe")
        result = _report_to_soql(described)
        self._probe_generated_soql(result)
        return result

    def _probe_generated_soql(self, result: dict) -> None:
        """
        Test-run each generated query with LIMIT 1 so the caller never receives SOQL that
        is syntactically plausible but rejected by the org (e.g. GROUP BY on a formula
        field, which reports allow but SOQL does not).
        """
        for key in ("soql", "aggregateSoql"):
            query = result.get(key)
            if not query:
                continue
            probe = query if " LIMIT " in query else query + " LIMIT 1"
            try:
                self.run_soql(probe)
            except SalesforceError as e:
                if key == "aggregateSoql":
                    # Reports allow constructs SOQL rejects (GROUP BY formula/text fields,
                    # SUM over checkboxes); the detail query is the reliable fallback.
                    del result["aggregateSoql"]
                    result["caveats"].append(
                        f"aggregateSoql was removed because the org rejected it: {e}. "
                        'Pull detail rows with "soql" instead and aggregate client-side.'
                    )
                else:
                    result["caveats"].append(
                        f"WARNING: the generated {key} FAILED a LIMIT 1 test run against "
                        f"the org and needs fixing before use: {e}"
                    )

    def get_dashboard(self, dashboard_id: str) -> dict:
        """Fetch a dashboard's full saveable representation (GET .../dashboards/<id>/describe)."""
        did = _resolve_id(dashboard_id, "dashboard")
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
        did = _resolve_id(dashboard_id, "dashboard")
        payload = self._prepare_dashboard(dashboard_metadata, for_update=True)
        return self._patch(f"/analytics/dashboards/{did}", payload)

    def delete_dashboard(self, dashboard_id: str) -> dict:
        """Delete a dashboard (DELETE /analytics/dashboards/<id>)."""
        did = _resolve_id(dashboard_id, "dashboard")
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
        if len(str(comp.get("title") or "")) > 40:
            raise SalesforceError(
                f"{where}.title exceeds Salesforce's 40-character limit "
                f"({len(str(comp['title']))} chars). Shorten it, or put longer text in "
                '"footer" or "header".'
            )
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


def _id_pattern(prefix: str) -> str:
    return rf"\b{prefix}[a-zA-Z0-9]{{12}}(?:[a-zA-Z0-9]{{3}})?\b"


def _resolve_id(value: str, kind: str) -> str:
    """
    Resolve a report/dashboard Id from a raw Id or any Salesforce URL containing one
    (Lightning /lightning/r/Report/<id>/view, Classic /<id>, builder URLs, ...).
    kind is 'report' or 'dashboard'; a pasted Id of the other kind raises a pointer
    to the right tool instead of a confusing downstream 404.
    """
    prefix = _KEY_PREFIXES[kind]
    v = (value or "").strip()
    m = re.search(_id_pattern(prefix), v)
    if m:
        return m.group(0)
    other = "dashboard" if kind == "report" else "report"
    if re.search(_id_pattern(_KEY_PREFIXES[other]), v):
        raise SalesforceError(
            f"{value!r} contains a {other} Id (prefix {_KEY_PREFIXES[other]}), not a {kind} Id "
            f"(prefix {prefix}). Use the {other} tools for it."
        )
    raise SalesforceError(
        f"Could not find a {kind} Id (15/18 chars starting with '{prefix}') in {value!r}. "
        f"Pass the Id itself or a Salesforce URL that contains it."
    )


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


# ---------------------------------------------------------------------------
# Report run compaction
# ---------------------------------------------------------------------------

_GROUPED_ROW_CAP = 200


def _flatten_grouping_tree(nodes: list | None, path: list | None = None) -> dict:
    """Flatten a run's nested groupings into {factMap key: [label, label, ...]}."""
    out = {}
    for node in nodes or []:
        labels = (path or []) + [node.get("label")]
        out[node.get("key")] = labels
        out.update(_flatten_grouping_tree(node.get("groupings"), labels))
    return out


def _is_leaf_key(key: str, n_groupings: int) -> bool:
    if n_groupings == 0:
        return key == "T"
    return key != "T" and key.count("_") + 1 == n_groupings


def _key_sort_tuple(key: str) -> tuple:
    return () if key == "T" else tuple(int(p) for p in key.split("_"))


def _compact_run(run: dict, detail_row_limit: int, overridden: list) -> dict:
    """
    Compact a raw sync-run response (which can be hundreds of KB) into leaf-level
    grouped rows, capped detail rows, grand totals, and actionable warnings.
    """
    rm = run.get("reportMetadata", {})
    ext = run.get("reportExtendedMetadata", {})
    agg_names = list(rm.get("aggregates", []))
    agg_info = ext.get("aggregateColumnInfo", {})
    agg_labels = [agg_info.get(a, {}).get("label") or a for a in agg_names]
    down_names = [g.get("name") for g in rm.get("groupingsDown", []) or []]
    across_names = [g.get("name") for g in rm.get("groupingsAcross", []) or []]
    down_labels = _flatten_grouping_tree((run.get("groupingsDown") or {}).get("groupings"))
    across_labels = _flatten_grouping_tree((run.get("groupingsAcross") or {}).get("groupings"))
    detail_columns = list(rm.get("detailColumns", []) or [])

    grouped, details = [], []
    details_available = 0
    for key, cell in (run.get("factMap") or {}).items():
        down_key, _, across_key = key.partition("!")
        if not (
            _is_leaf_key(down_key, len(down_names))
            and _is_leaf_key(across_key, len(across_names))
        ):
            continue
        if key != "T!T":
            row = {}
            for name, label in zip(down_names, down_labels.get(down_key, [])):
                row[name] = label
            for name, label in zip(across_names, across_labels.get(across_key, [])):
                row[name] = label
            for label, agg in zip(agg_labels, cell.get("aggregates", []) or []):
                row[label] = (agg or {}).get("value")
            grouped.append((_key_sort_tuple(down_key), _key_sort_tuple(across_key), row))
        rows = cell.get("rows") or []
        details_available += len(rows)
        for r in rows:
            if len(details) >= max(detail_row_limit, 0):
                continue
            values = [(c or {}).get("label") for c in r.get("dataCells", [])]
            detail = dict(zip(detail_columns, values))
            group_path = down_labels.get(down_key, []) + across_labels.get(across_key, [])
            if group_path:
                detail["_groupings"] = group_path
            details.append(detail)
    grouped.sort(key=lambda t: (t[0], t[1]))
    grouped_rows = [row for _, _, row in grouped]

    grand = (run.get("factMap") or {}).get("T!T", {})
    grand_totals = {
        label: (agg or {}).get("value")
        for label, agg in zip(agg_labels, grand.get("aggregates", []) or [])
    }

    warnings = []
    if run.get("allData") is False or run.get("hasExceededTabularRowLimit"):
        warnings.append(
            "PARTIAL DATA: the Analytics API caps synchronous report runs (~2000 detail "
            "rows / grouping limits), and this run hit the cap. For the full data set, use "
            "report_to_soql and run the generated SOQL with run_soql."
        )
    if len(grouped_rows) > _GROUPED_ROW_CAP:
        warnings.append(
            f"groupedRows truncated to {_GROUPED_ROW_CAP} of {len(grouped_rows)} leaf "
            "groups. Use report_to_soql + run_soql (aggregateSoql) for the full breakdown."
        )
        grouped_rows = grouped_rows[:_GROUPED_ROW_CAP]
    if details_available > len(details):
        warnings.append(
            f"detailRows shows {len(details)} of {details_available} rows returned by this "
            "run (detail_row_limit). Raise detail_row_limit or use report_to_soql for all rows."
        )
    row_count = grand_totals.get("Record Count")
    if row_count is None and "RowCount" in agg_names:
        idx = agg_names.index("RowCount")
        aggs = grand.get("aggregates", []) or []
        row_count = (aggs[idx] or {}).get("value") if idx < len(aggs) else None
    is_empty = not grouped_rows and not details and not row_count
    if is_empty and rm.get("scope") == "user":
        warnings.append(
            'EMPTY RESULT, LIKELY SCOPE: this report\'s scope is "user" ("My records"), '
            "which resolves to the API integration user -- not the person who owns the "
            "report -- so it returns nothing here even if it has data in the UI. Re-run "
            'with metadata_overrides={"scope": "organization"} (one-off, does not modify '
            "the saved report), optionally adding an owner filter via reportFilters, or "
            "use report_to_soql and add WHERE OwnerId = '<user id>'."
        )

    out = {
        "report": {
            "id": rm.get("id"),
            "name": rm.get("name"),
            "reportFormat": rm.get("reportFormat"),
            "reportType": rm.get("reportType"),
            "scope": rm.get("scope"),
            "standardDateFilter": rm.get("standardDateFilter"),
            "reportFilters": rm.get("reportFilters"),
            "reportBooleanFilter": rm.get("reportBooleanFilter"),
        },
        "allData": run.get("allData"),
        "groupings": {"down": down_names, "across": across_names},
        "aggregates": dict(zip(agg_names, agg_labels)),
        "grandTotals": grand_totals,
        "groupedRows": grouped_rows,
        "detailRows": details,
        "detailRowsAvailableInRun": details_available,
    }
    if overridden:
        out["overriddenForThisRunOnly"] = overridden
    if warnings:
        out["warnings"] = warnings
    return out


# ---------------------------------------------------------------------------
# Report -> SOQL conversion
# ---------------------------------------------------------------------------

_NUMERIC_TYPES = {"currency", "double", "percent", "int"}
_SOQL_FUNCS = {"s": "SUM", "a": "AVG", "m": "MIN", "x": "MAX"}
_DATE_GRANULARITY_FUNCS = {
    # granularity -> list of SOQL date functions (applied to the field, in order)
    "Day": [],
    "Month": ["CALENDAR_YEAR", "CALENDAR_MONTH"],
    "Quarter": ["CALENDAR_YEAR", "CALENDAR_QUARTER"],
    "Year": ["CALENDAR_YEAR"],
    "FiscalQuarter": ["FISCAL_YEAR", "FISCAL_QUARTER"],
    "FiscalYear": ["FISCAL_YEAR"],
    "Week": ["CALENDAR_YEAR", "WEEK_IN_YEAR"],
}


def _soql_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _soql_like_quote(value: str) -> str:
    # LIKE patterns additionally treat % and _ as wildcards.
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    escaped = escaped.replace("%", "\\%").replace("_", "\\_")
    return "'" + escaped + "'"


class _SoqlBuilder:
    """Builds best-effort SOQL from a report describe (reportMetadata + column metadata)."""

    def __init__(self, described: dict):
        self.rm = described.get("reportMetadata", {})
        ext = described.get("reportExtendedMetadata", {})
        rtm = described.get("reportTypeMetadata", {})
        self.colinfo: dict = {}
        for cat in rtm.get("categories", []):
            self.colinfo.update(cat.get("columns", {}))
        self.colinfo.update(ext.get("detailColumnInfo", {}))
        self.colinfo.update(ext.get("groupingColumnInfo", {}))
        self.cdf = self.rm.get("customDetailFormula") or {}
        self.base = next(
            (o.get("apiName") for o in rtm.get("objects", []) if o.get("joinType") == "ROOT"),
            None,
        )
        if not self.base:
            fq = next(
                (
                    (info.get("fullyQualifiedName") or "")
                    for info in self.colinfo.values()
                    if "." in (info.get("fullyQualifiedName") or "")
                ),
                "",
            )
            self.base = fq.split(".")[0] if fq else None
        self.caveats: list[str] = []
        self.unmapped: list[dict] = []

    def field_for(self, column: str) -> str | None:
        """Map a report column name to a SOQL field path, or record why it can't be."""
        if column in self.cdf:
            f = self.cdf[column]
            self._unmap(
                column,
                f"custom detail formula {f.get('label')!r} = {f.get('formula')} "
                "(report-only; recompute from the underlying fields)",
            )
            return None
        if column.startswith("BucketField"):
            self._unmap(column, "bucket field (report-only construct)")
            return None
        info = self.colinfo.get(column)
        if not info:
            self._unmap(column, "no column metadata in the report describe")
            return None
        fq = info.get("fullyQualifiedName") or info.get("entityColumnName") or ""
        segments = [s for s in fq.split(".") if s]
        if len(segments) > 1 and segments[0] == self.base:
            segments = segments[1:]
        if not segments:
            self._unmap(column, "no SOQL field mapping in the report describe")
            return None
        # Lookup traversals use the relationship name: Custom_Lookup__c -> Custom_Lookup__r.
        segments = [
            s[:-3] + "__r" if i < len(segments) - 1 and s.endswith("__c") else s
            for i, s in enumerate(segments)
        ]
        return ".".join(segments)

    def _unmap(self, column: str, reason: str) -> None:
        if not any(u["reportColumn"] == column for u in self.unmapped):
            self.unmapped.append({"reportColumn": column, "reason": reason})

    def _data_type(self, column: str) -> str:
        return ((self.colinfo.get(column) or {}).get("dataType") or "").lower()

    def _literal(self, value: str, data_type: str) -> str:
        v = value.strip()
        if data_type in _NUMERIC_TYPES:
            if re.fullmatch(r"-?\d+(\.\d+)?", v):
                return v
            self.caveats.append(
                f"Filter value {v!r} on a {data_type} column is not a plain number; "
                "quoted as a string, verify the clause."
            )
            return _soql_quote(v)
        if data_type == "boolean":
            return "true" if v.lower() in ("true", "1") else "false"
        if data_type == "date" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            return v
        if data_type == "datetime":
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                return f"{v}T00:00:00Z"
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.+Zz-]+", v):
                return v
        return _soql_quote(v)

    def _reference_field(self, field: str, values: list[str]) -> str:
        """Reference filters compare display names, so point at <relationship>.Name."""
        if all(re.fullmatch(r"[a-zA-Z0-9]{15}([a-zA-Z0-9]{3})?", v) for v in values if v):
            return field  # values are Ids; the raw Id field is correct
        head, _, last = field.rpartition(".")
        if last.endswith("Id"):
            last = last[:-2] + ".Name"
        elif last.endswith("__c"):
            last = last[:-3] + "__r.Name"
        else:
            last = last + ".Name"
        self.caveats.append(
            f"Filter on reference column mapped to {('%s.%s' % (head, last)) if head else last} "
            "because the report filter stores display names, not Ids."
        )
        return f"{head}.{last}" if head else last

    def render_filter(self, f: dict) -> str | None:
        column, op = f.get("column"), f.get("operator")
        raw = str(f.get("value") if f.get("value") is not None else "")
        field = self.field_for(column)
        if not field:
            self.caveats.append(f"Filter on {column!r} ({op} {raw!r}) NOT converted.")
            return None
        data_type = self._data_type(column)
        values = [p.strip() for p in raw.split(",")] if raw else [""]
        if len(values) > 1:
            self.caveats.append(
                f"Filter value {raw!r} on {column!r} was split on commas into OR values "
                "(report multi-value convention); verify none of the values themselves "
                "contain a comma."
            )
        if data_type == "reference":
            field = self._reference_field(field, values)
            data_type = "string"

        def lits() -> list[str]:
            return [self._literal(v, data_type) for v in values if v != ""]

        if op in ("equals", "notEqual"):
            if data_type == "multipicklist":
                joined = ",".join(lits())
                return f"{field} {'INCLUDES' if op == 'equals' else 'EXCLUDES'} ({joined})"
            non_empty = lits()
            has_blank = any(v == "" for v in values)
            parts = []
            if len(non_empty) == 1:
                parts.append(f"{field} {'=' if op == 'equals' else '!='} {non_empty[0]}")
            elif non_empty:
                parts.append(f"{field} {'IN' if op == 'equals' else 'NOT IN'} ({', '.join(non_empty)})")
            if has_blank or not non_empty:
                parts.append(f"{field} {'=' if op == 'equals' else '!='} null")
            joiner = " OR " if op == "equals" else " AND "
            return parts[0] if len(parts) == 1 else "(" + joiner.join(parts) + ")"
        if op in ("lessThan", "greaterThan", "lessOrEqual", "greaterOrEqual"):
            sym = {"lessThan": "<", "greaterThan": ">",
                   "lessOrEqual": "<=", "greaterOrEqual": ">="}[op]
            return f"{field} {sym} {self._literal(values[0], data_type)}"
        if op in ("contains", "notContain", "startsWith"):
            clauses = []
            for v in values:
                if v == "":
                    continue
                pattern = _soql_like_quote(v)
                pattern = pattern[0] + (
                    pattern[1:-1] + "%" if op == "startsWith" else "%" + pattern[1:-1] + "%"
                ) + pattern[-1]
                clauses.append(
                    f"(NOT {field} LIKE {pattern})" if op == "notContain"
                    else f"{field} LIKE {pattern}"
                )
            if not clauses:
                return None
            joiner = " AND " if op == "notContain" else " OR "
            return clauses[0] if len(clauses) == 1 else "(" + joiner.join(clauses) + ")"
        if op in ("includes", "excludes"):
            joined = ",".join(lits())
            return f"{field} {'INCLUDES' if op == 'includes' else 'EXCLUDES'} ({joined})"
        self.caveats.append(
            f"Filter on {column!r} uses operator {op!r} which has no SOQL translation; "
            "NOT converted."
        )
        return None

    def where_clause(self) -> str | None:
        filters = [f for f in self.rm.get("reportFilters", []) or [] if isinstance(f, dict)]
        rendered = [self.render_filter(f) for f in filters]
        logic = (self.rm.get("reportBooleanFilter") or "").strip()
        conditions = [c for c in rendered if c]
        if logic and re.fullmatch(r"[\d\sANDORNOT()]+", logic, re.IGNORECASE) and all(rendered):
            combined = re.sub(
                r"\d+", lambda m: "(" + rendered[int(m.group(0)) - 1] + ")", logic
            )
        else:
            if logic and not all(rendered):
                self.caveats.append(
                    f"Custom filter logic {logic!r} dropped because some filters could not "
                    "be converted; remaining filters are ANDed instead."
                )
            elif logic and not re.fullmatch(r"[\d\sANDORNOT()]+", logic, re.IGNORECASE):
                self.caveats.append(f"Unrecognized filter logic {logic!r}; filters ANDed.")
            combined = " AND ".join(f"({c})" for c in conditions) if conditions else None
        clauses = [combined] if combined else []
        clauses += self._standard_date_clauses()
        return " AND ".join(clauses) if clauses else None

    def _standard_date_clauses(self) -> list[str]:
        sdf = self.rm.get("standardDateFilter") or {}
        start, end = sdf.get("startDate"), sdf.get("endDate")
        if not (start or end):
            return []
        field = self.field_for(sdf.get("column"))
        if not field:
            self.caveats.append(
                f"Standard date filter on {sdf.get('column')!r} "
                f"({sdf.get('durationValue')}: {start}..{end}) NOT converted."
            )
            return []
        duration = sdf.get("durationValue")
        if duration and duration != "CUSTOM":
            self.caveats.append(
                f"Standard date filter {duration!r} was resolved to the FIXED range "
                f"{start}..{end} as of today; a relative window shifts over time, so "
                "regenerate (or swap in a SOQL date literal like THIS_FISCAL_YEAR) for "
                "future runs."
            )
        is_datetime = self._data_type(sdf.get("column")) == "datetime"
        clauses = []
        if start:
            clauses.append(f"{field} >= {start}T00:00:00Z" if is_datetime else f"{field} >= {start}")
        if end:
            clauses.append(f"{field} <= {end}T23:59:59Z" if is_datetime else f"{field} <= {end}")
        return clauses

    def build(self) -> dict:
        rm = self.rm
        if not self.base:
            raise SalesforceError(
                "Could not determine the report's base object from its describe; "
                "this report type is not convertible to SOQL automatically."
            )
        columns = []
        fields = ["Id"]
        groupings = list(rm.get("groupingsDown", []) or []) + list(
            rm.get("groupingsAcross", []) or []
        )
        for name in list(rm.get("detailColumns", []) or []) + [
            g.get("name") for g in groupings if isinstance(g, dict)
        ]:
            field = self.field_for(name)
            if field:
                info = self.colinfo.get(name, {})
                columns.append({
                    "reportColumn": name,
                    "soqlField": field,
                    "label": info.get("label"),
                    "dataType": info.get("dataType"),
                })
                if field not in fields:
                    fields.append(field)
        where = self.where_clause()

        scope = rm.get("scope")
        if scope in ("user", "mine"):
            self.caveats.append(
                'Report scope is "user" (My records): "my" means the RUNNING user, and via '
                "this API that is the integration user, so the saved report usually shows "
                "nothing. The SOQL below has NO owner filter (= all records); add "
                "\"OwnerId = '<005... user id>'\" to reproduce a specific person's view."
            )
        elif scope and scope != "organization":
            self.caveats.append(
                f"Report scope {scope!r} (queue/team/role-hierarchy style) is not "
                "expressible in SOQL; the query returns all records visible to the "
                "integration user."
            )
        cross = rm.get("crossFilters") or []
        if cross:
            self.caveats.append(
                f"{len(cross)} cross filter(s) NOT converted -- add matching "
                '"Id IN/NOT IN (SELECT <join field> FROM <related object>)" semi-join '
                "clauses by hand (raw definitions included as crossFilters)."
            )

        order_limit = ""
        top = rm.get("topRows") or {}
        sort_parts = []
        for s in rm.get("sortBy", []) or []:
            f = self.field_for(s.get("sortColumn"))
            if f:
                sort_parts.append(f"{f} {'DESC' if str(s.get('sortOrder', '')).lower().startswith('desc') else 'ASC'}")
        if sort_parts:
            order_limit += " ORDER BY " + ", ".join(sort_parts)
        if top.get("rowLimit"):
            order_limit += f" LIMIT {int(top['rowLimit'])}"
            self.caveats.append(
                f"The report keeps only the top {top['rowLimit']} rows (row limit); "
                "the LIMIT clause reproduces that."
            )

        soql = f"SELECT {', '.join(fields)} FROM {self.base}"
        if where:
            soql += f" WHERE {where}"
        soql += order_limit

        result = {
            "baseObject": self.base,
            "soql": soql,
            "columns": columns,
            "unmapped": self.unmapped,
            "caveats": self.caveats,
        }
        agg_soql = self._aggregate_soql(groupings, where)
        if agg_soql:
            result["aggregateSoql"] = agg_soql
        if cross:
            result["crossFilters"] = cross
        return result

    def _aggregate_soql(self, groupings: list, where: str | None) -> str | None:
        """A GROUP BY query mirroring the report's groupings and aggregates."""
        if not groupings:
            return None
        group_exprs = []
        for g in groupings:
            if not isinstance(g, dict):
                continue
            field = self.field_for(g.get("name"))
            if not field:
                self.caveats.append(
                    f"aggregateSoql omitted: grouping {g.get('name')!r} is not mappable."
                )
                return None
            gran = g.get("dateGranularity") or "None"
            data_type = self._data_type(g.get("name"))
            if gran != "None" and data_type in ("date", "datetime"):
                funcs = _DATE_GRANULARITY_FUNCS.get(gran)
                if funcs is None:
                    self.caveats.append(
                        f"aggregateSoql: unknown date granularity {gran!r} on {field}; "
                        "grouped by the raw field instead."
                    )
                    funcs = []
                group_exprs += [f"{fn}({field})" for fn in funcs] or [field]
            else:
                group_exprs.append(field)
        agg_exprs = []
        for a in self.rm.get("aggregates", []) or []:
            if a == "RowCount":
                agg_exprs.append("COUNT(Id)")
                continue
            m = re.fullmatch(r"([saxm])!(.+)", a)
            if not m:
                continue
            field = self.field_for(m.group(2))
            if field:
                agg_exprs.append(f"{_SOQL_FUNCS[m.group(1)]}({field})")
            else:
                self.caveats.append(f"aggregateSoql: aggregate {a!r} skipped (unmappable).")
        if not agg_exprs:
            agg_exprs = ["COUNT(Id)"]
        soql = f"SELECT {', '.join(group_exprs + agg_exprs)} FROM {self.base}"
        if where:
            soql += f" WHERE {where}"
        soql += f" GROUP BY {', '.join(group_exprs)}"
        return soql


def _report_to_soql(described: dict) -> dict:
    return _SoqlBuilder(described).build()


def get_client() -> SalesforceClient:
    """Return a Salesforce client authenticated via Client Credentials."""
    return SalesforceClient()
