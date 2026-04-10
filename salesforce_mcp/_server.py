"""MCP FastMCP app and tool definitions (used by server.run)."""

import json

from mcp.server.fastmcp import FastMCP

from salesforce_mcp.salesforce import SalesforceError, get_client

mcp = FastMCP("Salesforce", json_response=True)


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
    except SalesforceError as e:
        return _tool_error(str(e))
    except FileNotFoundError as e:
        return _tool_error(str(e))
    except ValueError as e:
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
    except SalesforceError as e:
        return _tool_error(str(e))
    except FileNotFoundError as e:
        return _tool_error(str(e))
    except ValueError as e:
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
    except SalesforceError as e:
        return _tool_error(str(e))
    except FileNotFoundError as e:
        return _tool_error(str(e))
    except ValueError as e:
        return _tool_error(str(e))
