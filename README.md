# Salesforce MCP Server (Read-Only)

An MCP server that provides **read-only** access to your Salesforce org: run SOQL queries, describe sObjects, and list all objects. Authentication uses the **OAuth 2.0 Client Credentials** flow. **No writes or DML** are allowed -- only SELECT queries and describe/list APIs.

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

## Tools (Read-Only)

| Tool | Description |
|------|-------------|
| **run_soql** | Execute a SOQL SELECT query. INSERT/UPDATE/DELETE/UPSERT/EXECUTE are rejected. |
| **describe_sobject** | Describe one sObject: fields, labels, types, relationships. |
| **list_objects** | List all sObjects in the org: name, label, custom flag. |

## License

MIT.
