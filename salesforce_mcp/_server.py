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
def create_report(report_metadata: dict[str, Any]) -> str:
    """
    Create a NEW report in Salesforce via the Analytics REST API
    (POST /services/data/<version>/analytics/reports).

    IMPORTANT: Only call this tool when the user has EXPLICITLY asked to create a report in
    Salesforce. This is a write operation that creates a new report asset in the org. Do not
    call it to read, query, or explore data (use run_soql / describe_sobject / list_objects for
    that), and never call it speculatively.

    Pass `report_metadata` as the object that goes under the request's "reportMetadata" key.
    Required fields: name, reportType, reportFormat. Typically also detailColumns and folderId.

    Example report_metadata:
        {
            "name": "Clay Audience Report",
            "reportType": {"type": "AccountList"},
            "reportFormat": "TABULAR",
            "detailColumns": ["ACCOUNT.NAME", "ACCOUNT.URL", "ACCOUNT.EMPLOYEES"],
            "folderId": "00lXXXXXXXXXXXX"
        }

    Returns the created report definition (including its new report Id) as JSON.
    """
    try:
        client = get_client()
        result = client.create_report(report_metadata)
        return json.dumps(result, default=str)
    except Exception as e:
        return _tool_error(str(e))
