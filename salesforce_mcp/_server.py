"""MCP FastMCP app and tool definitions (used by server.run)."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from salesforce_mcp.salesforce import get_client

mcp = FastMCP("Salesforce", json_response=True, stateless_http=True)


def _tool_error(message: str) -> str:
    """Format an error message for tool result."""
    return json.dumps({"error": message})


@mcp.tool()
def run_soql(query: str) -> str:
    """
    Execute a SOQL query (SELECT only). Returns query results including records, totalSize, and done.
    Supports LIMIT; if the result has nextRecordsUrl, only the first page is returned (you can run
    another query for more). Only read-only SELECT queries are allowed.
    """
    try:
        client = get_client()
        result = client.run_soql(query)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


@mcp.tool()
def describe_sobject(sobject: str) -> str:
    """
    Describe one sObject (standard or custom): fields, labels, types, relationships.
    Use list_objects to see available object names.
    """
    try:
        client = get_client()
        result = client.describe_sobject(sobject.strip())
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


@mcp.tool()
def list_objects() -> str:
    """
    List all sObjects (standard and custom) in the org. Returns name, label, and custom flag
    for each object so you can choose which to describe or query.
    """
    try:
        client = get_client()
        result = client.list_objects()
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


@mcp.tool()
def list_report_types() -> str:
    """
    List the report types available in the org, grouped by category. Returns each type's
    API name (e.g. "AccountList", "OpportunityList") and label. Read-only.

    ALWAYS call this (or describe_report_type, if the type is already known) before
    create_report so the reportType is valid.
    """
    try:
        client = get_client()
        result = client.list_report_types()
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


@mcp.tool()
def describe_report_type(report_type: str) -> str:
    """
    Describe one report type: the exact column names usable in a report's detailColumns,
    groupings, and filters, grouped by category with label and dataType. Picklist columns
    include filterValues (the exact values accepted by reportFilters). filterOperators maps
    each dataType to its valid filter operator names. Read-only.

    Report column names are NOT SOQL field names (e.g. "ACCOUNT.NAME", not "Name"), so
    ALWAYS call this before create_report and copy column names exactly from the result.
    Use list_report_types to find valid report_type values (e.g. "AccountList").

    Also returns "scopes": the valid values for reportMetadata.scope (e.g. "user" = my
    records, "organization" = all records) with the org default.
    """
    try:
        client = get_client()
        result = client.describe_report_type(report_type)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


@mcp.tool()
def create_report(report_metadata: dict[str, Any]) -> str:
    """
    Create a NEW report in Salesforce via the Analytics REST API
    (POST /services/data/<version>/analytics/reports).

    IMPORTANT: Only call this tool when the user has EXPLICITLY asked to create a report in
    Salesforce. This is a write operation that creates a new report asset in the org. Do not
    call it to read, query, or explore data (use run_soql / describe_sobject / list_objects for
    that), and never call it speculatively.

    WORKFLOW (required for a correct call):
      1. list_report_types -> find the reportType API name.
      2. describe_report_type(<type>) -> copy exact column names for detailColumns,
         groupings, and filters, plus picklist filterValues and valid operators.
      3. create_report with those values.
    The payload (columns, groupings, filter columns, picklist filter values) is validated
    against the report type describe before anything is sent to Salesforce; invalid input
    returns an error listing valid options.

    Pass `report_metadata` as the object that goes under the request's "reportMetadata" key.
    Required fields: name, reportType, reportFormat (TABULAR | SUMMARY | MATRIX). Typically
    also detailColumns. If folderId is omitted, the report is created in the default public
    'Claude Reports' folder.

    Filters: reportFilters is a list of {"column", "operator", "value"}. For multiple
    picklist values (OR), pass ONE filter with comma-separated values, e.g.
    {"column": "TYPE", "operator": "equals", "value": "Net New,New Business"} -- do NOT
    create one filter per value (multiple filters are ANDed via reportBooleanFilter).

    Groupings (SUMMARY/MATRIX only): groupingsDown / groupingsAcross is a list of
    {"name": <column>, "sortOrder": "Asc"|"Desc", "dateGranularity": <granularity>}.
    dateGranularity (date columns only): Day, Week, Month, Quarter, Year,
    FiscalQuarter, FiscalYear. TABULAR reports must NOT have groupings.

    Aggregates: aggregates is a list of strings: "s!<COLUMN>" sum, "a!<COLUMN>" average,
    "m!<COLUMN>" min, "x!<COLUMN>" max (numeric columns only), plus "RowCount".
    Example summary report: {"reportFormat": "SUMMARY",
        "groupingsDown": [{"name": "CREATED_DATE", "sortOrder": "Asc",
                           "dateGranularity": "Month"}],
        "aggregates": ["s!AMOUNT", "RowCount"], ...}

    Scope and date range (the "report looks empty" trap): Salesforce defaults new reports
    to the author's own records ("scope": "user") and a created-this-week date window,
    which renders EMPTY for other users and on dashboards (dashboards run as a fixed user).
    Unless you pass them, this tool defaults scope to "organization" (all records, when the
    report type supports it) and widens the standard date filter to All Time. Pass "scope"
    (valid values are in describe_report_type's "scopes") or "standardDateFilter"
    ({"column", "durationValue", "startDate", "endDate"}) explicitly to override.

    Example report_metadata (tabular):
        {
            "name": "Clay Audience Report",
            "reportType": {"type": "AccountList"},
            "reportFormat": "TABULAR",
            "detailColumns": ["ACCOUNT.NAME", "URL", "EMPLOYEES"]
        }

    Returns the created report definition (including its new report Id) as JSON.
    Use get_report / update_report / delete_report to inspect, fix, or remove it.
    """
    try:
        client = get_client()
        result = client.create_report(report_metadata)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


@mcp.tool()
def get_report(report_id: str) -> str:
    """
    Get a report's saveable metadata (name, reportType, columns, groupings, filters, scope,
    standardDateFilter, folderId) by Id via GET /analytics/reports/<id>/describe. Read-only.

    The result is in the shape accepted by create_report / update_report. Find report Ids
    with run_soql: SELECT Id, Name, FolderName FROM Report.
    """
    try:
        client = get_client()
        result = client.describe_report(report_id)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


@mcp.tool()
def update_report(report_id: str, report_metadata: dict[str, Any]) -> str:
    """
    Update an EXISTING report via PATCH /analytics/reports/<id>.

    IMPORTANT: Only call this when the user has explicitly asked to change a report.
    This overwrites report settings in the org.

    report_metadata may be PARTIAL: only the keys you pass change, everything else is
    kept. Common fixes: {"scope": "organization"} to widen from "my records" to all
    records, or {"standardDateFilter": {"column": "CREATED_DATE", "durationValue":
    "CUSTOM", "startDate": null, "endDate": null}} for All Time. Columns, groupings, and
    filters are validated against the report type (same rules as create_report).
    """
    try:
        client = get_client()
        result = client.update_report(report_id, report_metadata)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


@mcp.tool()
def delete_report(report_id: str) -> str:
    """
    Permanently DELETE a report by Id (DELETE /analytics/reports/<id>).

    DESTRUCTIVE and not undoable via the API: only call this when the user has explicitly
    asked to delete this specific report. A report referenced by dashboard components
    should not be deleted without also fixing the dashboard. Confirm the target with
    get_report first if there is any ambiguity.
    """
    try:
        client = get_client()
        result = client.delete_report(report_id)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


_DASHBOARD_SCHEMA_DOC = """
    SCHEMA (exact; unknown fields are silently DROPPED by Salesforce, which yields an
    empty-shell dashboard -- this tool rejects unknown keys up front for that reason):
        {
            "name": "Sales Overview",                       # required on create
            "description": "...",                           # optional
            "folderId": "00l...",                           # optional, defaults to 'Claude Dashboards'
            "components": [                                 # required, non-empty
                {
                    "type": "Report",                       # optional, defaults to "Report"
                    "reportId": "00O...",                   # required: an EXISTING report Id
                    "header": "ARR by Stage",               # shown above the widget
                    "title": "optional subtitle",
                    "properties": {
                        "visualizationType": "Column",      # required, see below
                        "aggregates": [{"name": "s!AMOUNT"}],
                        "groupings": [{"name": "STAGE_NAME", "sortOrder": "Asc"}]
                    }
                }
            ],
            "layout": {                                     # optional: omit for an automatic
                "gridLayout": true,                         # two-across grid
                "numColumns": 12,
                "rowHeight": 36,
                "components": [                             # ONE entry per component, matched
                    {"column": 0, "row": 0,                 # BY INDEX (not by id/name)
                     "colspan": 6, "rowspan": 12}
                ]
            }
        }

    visualizationType values and their rules (validated against the report before posting):
      - Charts (Bar, Column, Line, Donut, Pie, Funnel, Scatter): the report must be
        SUMMARY/MATRIX. "groupings" names must be groupings OF THE REPORT, and
        "aggregates" names must be aggregates of the report (e.g. "s!AMOUNT", "RowCount").
        If omitted, both default to the report's own groupings/first aggregate.
      - FlexTable (modern table): shows the report's detail columns. Set
        properties.visualizationProperties.tableColumns to
        [{"column": "<detail column>", "type": "detail"}, ...] (plain strings also accepted);
        defaults to all of the report's detailColumns. FlexTable can NOT mix detail columns
        with groupings/aggregates -- those are forced empty.
      - Table (classic table): works on any report, auto-selects columns; "groupings"
        optional.
      - Gauge / Metric: single-value; give one aggregate, no groupings.

    Things that look right but are WRONG (silently dropped or parser errors):
      - top-level "gridLayout" -> the layout lives under "layout" (shape above)
      - component "componentData" object -> component fields are flat (reportId at the
        component level, chart settings under "properties")
      - layout entries keyed "colIndex"/"rowIndex"/"colSpan"/"rowSpan"/"componentIndex"
        -> the keys are "column", "row", "colspan", "rowspan"; order matches components
"""


@mcp.tool()
def create_dashboard(dashboard_metadata: dict[str, Any]) -> str:
    """
    Create a NEW dashboard in Salesforce via the Analytics REST API
    (POST /services/data/<version>/analytics/dashboards).

    IMPORTANT: Only call this tool when the user has EXPLICITLY asked to create a dashboard in
    Salesforce. This is a write operation that creates a new dashboard asset in the org. Do not
    call it to read, query, or explore data, and never call it speculatively.

    WORKFLOW (required for a correct call):
      1. Create the reports first with create_report (or get existing report Ids) -- every
         dashboard component references an existing report Id.
      2. Charts render a report's GROUPINGS, so reports feeding charts must be
         SUMMARY/MATRIX with the grouping you want to plot.
      3. Call create_dashboard. The payload is validated against each report's describe
         before anything is sent; errors list the valid options.

    Returns the created dashboard (including its Id) as JSON. Verify the response's
    "components" is non-empty; a "warning" field is added if any component was dropped.
    Use get_dashboard / update_dashboard / delete_dashboard to inspect, fix, or remove it.
    """ + _DASHBOARD_SCHEMA_DOC
    try:
        client = get_client()
        result = client.create_dashboard(dashboard_metadata)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


@mcp.tool()
def get_dashboard(dashboard_id: str) -> str:
    """
    Get a dashboard's full definition (components, layout, folder) by Id via
    GET /analytics/dashboards/<id>/describe. Read-only.

    The result is in exactly the shape accepted by create_dashboard / update_dashboard,
    so use it as a reference or as the base for an update. Find dashboard Ids with
    run_soql: SELECT Id, Title, FolderName FROM Dashboard.
    """
    try:
        client = get_client()
        result = client.get_dashboard(dashboard_id)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


@mcp.tool()
def update_dashboard(dashboard_id: str, dashboard_metadata: dict[str, Any]) -> str:
    """
    Update an EXISTING dashboard via PATCH /analytics/dashboards/<id>.

    IMPORTANT: Only call this when the user has explicitly asked to change a dashboard.
    This overwrites the dashboard asset in the org.

    Pass only the fields to change: {"name": "..."} renames; passing "components" REPLACES
    the dashboard's entire component set (send ALL components you want to keep -- fetch the
    current ones with get_dashboard first -- and a matching "layout"). Same schema and
    validation as create_dashboard.
    """ + _DASHBOARD_SCHEMA_DOC
    try:
        client = get_client()
        result = client.update_dashboard(dashboard_id, dashboard_metadata)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


@mcp.tool()
def delete_dashboard(dashboard_id: str) -> str:
    """
    Permanently DELETE a dashboard by Id (DELETE /analytics/dashboards/<id>).

    DESTRUCTIVE and not undoable via the API: only call this when the user has explicitly
    asked to delete this specific dashboard (e.g. cleaning up an empty shell or a failed
    attempt). Confirm the target with get_dashboard first if there is any ambiguity.
    """
    try:
        client = get_client()
        result = client.delete_dashboard(dashboard_id)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))
