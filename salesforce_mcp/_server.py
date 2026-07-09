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

    Example report_metadata (tabular):
        {
            "name": "Clay Audience Report",
            "reportType": {"type": "AccountList"},
            "reportFormat": "TABULAR",
            "detailColumns": ["ACCOUNT.NAME", "URL", "EMPLOYEES"]
        }

    Returns the created report definition (including its new report Id) as JSON.
    """
    try:
        client = get_client()
        result = client.create_report(report_metadata)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))


@mcp.tool()
def create_dashboard(dashboard_metadata: dict[str, Any]) -> str:
    """
    Create a NEW dashboard in Salesforce via the Analytics REST API
    (POST /services/data/<version>/analytics/dashboards).

    IMPORTANT: Only call this tool when the user has EXPLICITLY asked to create a dashboard in
    Salesforce. This is a write operation that creates a new dashboard asset in the org. Do not
    call it to read, query, or explore data (use run_soql / describe_sobject / list_objects for
    that), and never call it speculatively.

    Pass `dashboard_metadata` as the full dashboard representation sent as the request body
    (unlike create_report, there is no wrapper key). Required field: name. If folderId is
    omitted, the dashboard is created in the default public 'Claude Dashboards' folder. Each
    dashboard component references an existing report Id, so create the reports first (or ask
    the user for existing report Ids).

    Example dashboard_metadata:
        {
            "name": "Sales Overview",
            "components": [
                {
                    "componentData": {
                        "reportId": "00OXXXXXXXXXXXX",
                        "visualizationType": "Column",
                        "displayUnits": "Auto"
                    },
                    "header": "Opportunities by Stage"
                }
            ],
            "gridLayout": {
                "rowCount": 10,
                "numColumns": 9,
                "widgets": [
                    {"componentIndex": 0, "colIndex": 0, "rowIndex": 0, "colSpan": 3, "rowSpan": 4}
                ]
            }
        }

    Returns the created dashboard definition (including its new dashboard Id) as JSON.
    """
    try:
        client = get_client()
        result = client.create_dashboard(dashboard_metadata)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))
