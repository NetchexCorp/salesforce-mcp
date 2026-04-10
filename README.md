# Salesforce MCP Server (Read-Only)

A local MCP server that connects to **Claude Desktop** via stdio and provides **read-only** access to your Salesforce org: run SOQL queries, describe sObjects (standard and custom), and list all objects. Authentication uses **OAuth 2.0** (Web Server flow). **No writes or DML** are allowed—only SELECT queries and describe/list APIs.

## Requirements

- Python 3.10+
- A Salesforce **Connected App** (see below)
- Claude Desktop

## Connected App (Salesforce)

1. In Salesforce: **Setup → App Manager → New Connected App** (or edit an existing one).
2. Enable **OAuth 2.0** and **OAuth 2.0 Web Server Flow**.
3. Set **Callback URL** to: `http://localhost:8765/callback` (or the port you use for auth).
4. Under **Selected OAuth Scopes**, add at least:
   - **Access and manage your data (api)**
   - **Perform requests at any time (refresh_token)**
5. Enable **Allow refresh token**.
6. Save and note your **web credentials** (OAuth credentials):
   - **Consumer Key** — use as Client ID below
   - **Consumer Secret** — use as Client Secret below  
   (In Setup they may appear under "Consumer Key" / "Consumer Secret" or "Web credentials".)

## One-Time OAuth Login

Before using the MCP server, run the auth script once so it can save your tokens. Set credentials as environment variables (these same values go in your Claude Desktop config):

```bash
export SALESFORCE_CLIENT_ID="your_consumer_key"
export SALESFORCE_CLIENT_SECRET="your_consumer_secret"

python -m salesforce_mcp.auth
```

Tokens are saved to `tokens.json` in the project root by default. To use a custom path:

```bash
export SALESFORCE_MCP_TOKEN_FILE="custom/path/tokens.json"   # relative to project root
python -m salesforce_mcp.auth
```

**Options:**

- `--port 8766` — Use a different callback port (must match the callback URL in your Connected App).
- `--sandbox` — Use `test.salesforce.com` (sandbox) instead of production.

## Claude Desktop Configuration

Add the MCP server to your Claude Desktop config. All credentials are stored in the `env` section.

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "salesforce": {
      "command": "uv",
      "args": ["run", "--project", "salesforce-mcp", "-m", "salesforce_mcp"],
      "env": {
        "SALESFORCE_CLIENT_ID": "your_consumer_key",
        "SALESFORCE_CLIENT_SECRET": "your_consumer_secret",
        "SALESFORCE_MCP_TOKEN_FILE": "tokens.json"
      }
    }
  }
}
```

> **Note:** `SALESFORCE_MCP_TOKEN_FILE` defaults to `tokens.json` (relative to the project root). You only need to set it if you want a different location. Relative paths are resolved from the project root; absolute paths are used as-is.

Restart Claude Desktop after changing the config. You should see the Salesforce tools (e.g. run_soql, describe_sobject, list_objects) available.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SALESFORCE_CLIENT_ID` or `SALESFORCE_CONSUMER_KEY` | Yes (for auth & refresh) | Connected App **Consumer Key** (web credentials) |
| `SALESFORCE_CLIENT_SECRET` or `SALESFORCE_CONSUMER_SECRET` | Yes (for auth & refresh) | Connected App **Consumer Secret** (web credentials) |
| `SALESFORCE_MCP_TOKEN_FILE` | No | Token file path (default: `tokens.json` relative to project root) |
| `SALESFORCE_CALLBACK_PORT` | No | Callback port for auth (default: 8765) |

Client ID and secret are stored in both the token file and the Claude Desktop config `env`. The server reads them from env during token refresh.

## Tools (Read-Only)

| Tool | Description |
|------|-------------|
| **run_soql** | Execute a SOQL query. Only **SELECT** queries are allowed; INSERT/UPDATE/DELETE/UPSERT/EXECUTE are rejected. Returns `records`, `totalSize`, `done`, and optionally `nextRecordsUrl`. |
| **describe_sobject** | Describe one sObject (standard or custom): fields, labels, types, relationships. |
| **list_objects** | List all sObjects in the org (Describe Global): name, label, custom flag, etc. |

No create/update/delete tools are exposed; the server is read-only.

## Install (Development)

From the project root:

```bash
pip install -e .
# or
uv pip install -e .
```

Then run auth once and add the server to Claude Desktop as above.

## License

MIT.
