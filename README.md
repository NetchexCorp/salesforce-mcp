# Salesforce MCP Server

An MCP server for your Salesforce org: run SOQL queries, describe sObjects, list all objects, and create reports. Authentication uses the **OAuth 2.0 Client Credentials** flow.

Data access is **read-only** -- SOQL is restricted to `SELECT` (INSERT/UPDATE/DELETE/UPSERT/EXECUTE are rejected). The only write operations are the report and dashboard create/update/delete tools, which are gated to run only when explicitly requested (see [Tools](#tools)).

Supports two transport modes:
- **stdio** -- for local use with Claude Desktop
- **Streamable HTTP** -- for remote deployment (Docker, Azure Container Apps, managed Claude agents)

## Requirements

- Python 3.10+
- A Salesforce **Connected App** configured for the Client Credentials flow

## Connected App Setup (Salesforce)

1. In Salesforce: **Setup > App Manager > New Connected App**.
2. Enable **OAuth Settings**.
3. Under **Selected OAuth Scopes**, add **Access and manage your data (api)**.
4. Enable **Client Credentials Flow**.
5. Save and note your **Consumer Key** and **Consumer Secret**.
6. Under **Manage > Edit Policies**, set the **Client Credentials** run-as user.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SALESFORCE_CLIENT_ID` | Yes | Connected App Consumer Key |
| `SALESFORCE_CLIENT_SECRET` | Yes | Connected App Consumer Secret |
| `SALESFORCE_LOGIN_HOST` | No | `login.salesforce.com` (default) or your My Domain host |
| `MCP_HOST` | No | Server bind address (default: `0.0.0.0`) |
| `MCP_PORT` | No | Server port (default: `8765`) |
| `MCP_API_KEY` | No | API key for Bearer token auth on the `/mcp` endpoint. When unset, auth is disabled. |
| `MCP_ALLOWED_HOSTS` | No | Comma-separated allowed Host headers, or `*` to disable DNS rebinding protection (recommended for cloud deployments with API key auth). |
| `MCP_TRANSPORT` | No | Transport mode when no CLI arg is given (`stdio` or `streamable-http`, default: `stdio`). |

## Running Locally (stdio)

```bash
pip install .
python -m salesforce_mcp
```

This starts the server in stdio mode, suitable for Claude Desktop.

### Claude Desktop Configuration

```json
{
  "mcpServers": {
    "salesforce": {
      "command": "python",
      "args": ["-m", "salesforce_mcp"],
      "env": {
        "SALESFORCE_CLIENT_ID": "<your_consumer_key>",
        "SALESFORCE_CLIENT_SECRET": "<your_consumer_secret>",
        "SALESFORCE_LOGIN_HOST": "<your_login_host>"
      }
    }
  }
}
```

## Running Locally (Streamable HTTP)

```bash
python -m salesforce_mcp streamable-http
```

The server starts on `http://0.0.0.0:8765/mcp` with a health check at `/health`.

## Running with Docker

```bash
docker compose up --build
```

Pass credentials via a `.env` file in the project root. The Dockerfile runs the server in Streamable HTTP mode by default.

## Deploying to Azure Container Apps

1. Build and push the Docker image to your Azure Container Registry:

   ```bash
   az acr login --name <your_acr>
   docker build --platform linux/amd64 -t <your_acr>.azurecr.io/salesforce-mcp:latest .
   docker push <your_acr>.azurecr.io/salesforce-mcp:latest
   ```

2. Create the container app:

   ```bash
   az containerapp create \
     --name salesforce-mcp \
     --resource-group <your_rg> \
     --environment <your_cae> \
     --image <your_acr>.azurecr.io/salesforce-mcp:latest \
     --registry-server <your_acr>.azurecr.io \
     --target-port 8765 \
     --ingress external \
     --min-replicas 0 \
     --max-replicas 3 \
     --cpu 0.25 \
     --memory 0.5Gi \
     --env-vars \
       SALESFORCE_CLIENT_ID=secretref:salesforce-client-id \
       SALESFORCE_CLIENT_SECRET=secretref:salesforce-client-secret \
       SALESFORCE_LOGIN_HOST=<your_login_host> \
       MCP_HOST=0.0.0.0 \
       MCP_PORT=8765 \
       MCP_API_KEY=secretref:mcp-api-key \
       MCP_ALLOWED_HOSTS="*" \
     --secrets \
       salesforce-client-id="<your_client_id>" \
       salesforce-client-secret="<your_client_secret>" \
       mcp-api-key="<your_api_key>"
   ```

3. Connect your MCP client to the deployed endpoint:

   ```json
   {
     "mcpServers": {
       "salesforce": {
         "type": "streamable-http",
         "url": "https://<your_fqdn>/mcp",
         "headers": {
           "Authorization": "Bearer <your_api_key>"
         }
       }
     }
   }
   ```

## API Key Authentication

When `MCP_API_KEY` is set, all requests to `/mcp` must include an `Authorization: Bearer <key>` header. Requests without a valid key receive a `401 Unauthorized` response.

The `/health` endpoint is always unauthenticated so that platform health probes (Azure, Docker, etc.) work without credentials.

When `MCP_API_KEY` is not set, all requests pass through without auth -- suitable for local development.

## Tools

| Tool | Description |
|------|-------------|
| **run_soql** | Execute a SOQL SELECT query. INSERT/UPDATE/DELETE/UPSERT/EXECUTE are rejected. |
| **describe_sobject** | Describe one sObject: fields, labels, types, relationships. |
| **list_objects** | List all sObjects in the org: name, label, custom flag. |
| **list_report_types** | List report types available in the org (API name + label, grouped by category). |
| **describe_report_type** | Describe one report type: valid column names for detailColumns/groupings/filters, picklist filter values, and filter operators per data type. |
| **create_report** | Create a new report via the Analytics REST API (`POST /analytics/reports`). **Write operation** -- called only when the user explicitly asks to create a report. |
| **get_report** | Fetch a report's saveable metadata (`GET /analytics/reports/<id>/describe`) in the shape accepted by create/update. |
| **update_report** | Update an existing report (`PATCH /analytics/reports/<id>`). Partial metadata: only the passed keys change. **Write operation.** |
| **delete_report** | Permanently delete a report (`DELETE /analytics/reports/<id>`). **Destructive** -- called only on an explicit user request. |
| **create_dashboard** | Create a new dashboard via the Analytics REST API (`POST /analytics/dashboards`). **Write operation** -- called only when the user explicitly asks to create a dashboard. |
| **get_dashboard** | Fetch a dashboard's full definition (`GET /analytics/dashboards/<id>/describe`) in the exact shape accepted by create/update. |
| **update_dashboard** | Update an existing dashboard (`PATCH /analytics/dashboards/<id>`). Passing `components` replaces the whole component set. **Write operation.** |
| **delete_dashboard** | Permanently delete a dashboard (`DELETE /analytics/dashboards/<id>`). **Destructive** -- called only on an explicit user request. |

### Default folders

When `folderId` is omitted, reports go to a public **Claude Reports** folder and dashboards to a public **Claude Dashboards** folder (looked up by `DeveloperName` at create time). Salesforce folders are typed (report vs. dashboard), so there are two. Create them once per org:

```bash
sf data create record --sobject Folder --values "Name='Claude Reports' DeveloperName='Claude_Reports' AccessType='Public' Type='Report'"
sf data create record --sobject Folder --values "Name='Claude Dashboards' DeveloperName='Claude_Dashboards' AccessType='Public' Type='Dashboard'"
```

If the folder is missing, the tool returns an error that includes the command above. An explicit `folderId` in the metadata always takes precedence.

### create_report

Creates a new report asset in the org. Pass `report_metadata` as the object placed under the request's `reportMetadata` key (`name`, `reportType`, and `reportFormat` are required; `detailColumns` is typical; `folderId` defaults to the Claude Reports folder):

```json
{
  "name": "Clay Audience Report",
  "reportType": { "type": "AccountList" },
  "reportFormat": "TABULAR",
  "detailColumns": ["ACCOUNT.NAME", "URL", "EMPLOYEES"]
}
```

To make the call one-shottable, the server validates the payload against the report type's describe **before** posting: unknown report types, column names (in `detailColumns`, groupings, or filters), and picklist filter values are rejected with the valid options (and close-match suggestions) in the error message. Salesforce API error bodies are also passed through verbatim so the caller can self-correct. The intended workflow is `list_report_types` -> `describe_report_type` -> `create_report`.

Report column names are report-type-specific (not SOQL field names). Multi-value picklist filters take one filter with comma-separated values (`"value": "Net New,Cross-sell"`). Groupings (`groupingsDown`/`groupingsAcross`, SUMMARY/MATRIX only) take `{"name", "sortOrder", "dateGranularity"}`; aggregates use prefixes (`s!AMOUNT` sum, `a!` avg, `m!` min, `x!` max, plus `RowCount`).

Scope and date range: Salesforce defaults new reports to the author's own records (`"scope": "user"`) and a created-this-week date window -- both of which make the report (and any dashboard built on it) render **empty** for other users, since dashboards run as a fixed user. Unless the caller passes them, the server defaults `scope` to `organization` (when the report type supports it) and widens `standardDateFilter` to All Time (`durationValue: "CUSTOM"` with null dates). `describe_report_type` returns the valid `scope` values per report type.

This requires the Connected App's run-as user to have permission to create reports in the target folder.

### create_dashboard / update_dashboard

Creates (or updates) a dashboard asset in the org. Pass `dashboard_metadata` as the full dashboard representation sent as the request body (no wrapper key -- unlike reports). `name` is required on create; `folderId` defaults to the Claude Dashboards folder; components reference existing report Ids, so create the reports first:

```json
{
  "name": "Sales Overview",
  "components": [
    {
      "reportId": "00OXXXXXXXXXXXX",
      "header": "Opportunities by Stage",
      "properties": {
        "visualizationType": "Column",
        "aggregates": [{ "name": "s!AMOUNT" }],
        "groupings": [{ "name": "STAGE_NAME", "sortOrder": "Asc" }]
      }
    }
  ],
  "layout": {
    "gridLayout": true,
    "numColumns": 12,
    "rowHeight": 36,
    "components": [
      { "column": 0, "row": 0, "colspan": 6, "rowspan": 12 }
    ]
  }
}
```

This shape was verified empirically against v62.0 -- it is what `GET /analytics/dashboards/<id>/describe` returns, and it is NOT what some docs suggest. Salesforce's parser **silently drops unknown fields**, so a payload with a top-level `gridLayout`, a `componentData` object, or `colIndex`/`rowSpan`-style layout keys "succeeds" but creates an **empty-shell dashboard with no components**. To make the call one-shottable the server rejects unknown keys up front, validates every component against its report's describe (chart `groupings`/`aggregates` must exist on the report; `FlexTable` `tableColumns` must be report detail columns and can't mix with groupings/aggregates), fills sensible defaults (groupings/aggregates from the report, `tableColumns` from `detailColumns`, an automatic two-across grid when `layout` is omitted), and warns if Salesforce persists fewer components than were sent.

Chart components (`Bar`, `Column`, `Line`, `Donut`, `Pie`, `Funnel`, `Scatter`) plot a report's groupings, so they need a SUMMARY/MATRIX report. `FlexTable` (Lightning table) shows detail columns; `Table` is the classic auto-column table; `Gauge`/`Metric` show a single aggregate. `layout.components` has one `{column, row, colspan, rowspan}` entry per component, matched by index.

This requires the Connected App's run-as user to have permission to create dashboards in the target folder ("Create and Customize Dashboards", plus "View Dashboards in Public Folders" as applicable). Dashboards run as that user, so it also needs field access to the reports' columns.

## License

MIT.
