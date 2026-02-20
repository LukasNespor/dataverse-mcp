# Dataverse MCP Server

An MCP server that connects Claude to Microsoft Dataverse via the Dataverse Web API v9.2.
Designed for everyday users who work with Dataverse records — not for admin operations like
managing environments, solutions, or security roles. Authenticates via interactive browser
sign-in (no client secrets required) with persistent token and schema caching.

---

## Available Tools

| Tool | Cached | Description |
|---|---|---|
| `Sign in to Dataverse` | — | Start interactive browser sign-in; returns a URL, token exchange happens automatically on redirect |
| `Get my identity` | Permanent | Get the current user's Dataverse GUID, business unit, and org ID |
| `Get table schema` | 1 hour TTL | Retrieve field definitions, types, and required fields per table |
| `Refresh schema cache` | — | Force a fresh schema fetch; use if schema seems stale after customizations |
| `List records` | — | Query records with OData $filter, $select, $orderby, and pagination |
| `Create record` | — | Create a new record (always call `Get table schema` first) |
| `Update record` | — | Partially update an existing record via PATCH |
| `Delete record` | — | Permanently delete a record |

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose installed
- A Microsoft Dataverse / Power Platform environment URL
- A Microsoft account with access to that environment

---

## Setup

### 1. Clone and configure

```bash
git clone <repo-url>
cd dataverse-mcp
cp .env.example .env
```

Edit `.env` and set at minimum:

```env
DATAVERSE_URL=https://yourorg.crm4.dynamics.com
CLIENT_ID=your-client-id-here
```

`CLIENT_ID` is required — register an app in Azure Portal (see [Registering Your Own Azure AD App](#registering-your-own-azure-ad-app)).

### 2. Build the Docker image

```bash
docker compose build
```

---

## Connecting to Claude Desktop

Claude Desktop launches the MCP server as a subprocess via stdio each time it starts.

### Configure Claude Desktop

Find your config file:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add the following, replacing the path with the absolute path to your cloned repo:

```json
{
  "mcpServers": {
    "dataverse": {
      "command": "docker",
      "args": [
        "compose",
        "-f", "/absolute/path/to/dataverse-mcp/docker-compose.yml",
        "run", "--rm", "-i", "--service-ports",
        "dataverse-mcp"
      ]
    }
  }
}
```

**Windows path example:**
```json
"args": [
  "compose",
  "-f", "C:/Users/yourname/dataverse-mcp/docker-compose.yml",
  "run", "--rm", "-i", "--service-ports",
  "dataverse-mcp"
]
```

### Restart Claude Desktop

Fully quit and reopen Claude Desktop. The Dataverse server will appear in the tools list (hammer icon).

### First sign-in (Claude Desktop)

Start a conversation and ask Claude to do something with Dataverse, e.g.:

> "Show me my recent contacts in Dataverse"

Claude will call a Dataverse tool, get an authentication error, and automatically call
`Sign in to Dataverse`. A sign-in URL will appear — open it in your browser and sign in
with your Microsoft account. Your browser will show "Authentication complete" when done.
Tell Claude you've signed in and it will proceed with your request.

You will not need to sign in again until the refresh token expires (Microsoft default: 90 days of inactivity).

---

## Connecting to Claude Code

Claude Code uses a persistent MCP server process rather than spawning a new one per session.
The server runs as a long-lived daemon and Claude Code connects to it over HTTP/SSE.

### Step 1 — Start the server in SSE mode

The default transport is stdio (for Claude Desktop). For Claude Code you need the server
running and listening on a port. The repo includes `docker-compose.sse.yml` which overrides
the transport to SSE and publishes port 8199. Start it with:

```bash
docker compose -f docker-compose.yml -f docker-compose.sse.yml up -d
```

Verify it is running:

```bash
curl http://localhost:8199/sse
# Should return an SSE stream (hang open) — Ctrl+C to exit
```

### Step 2 — Add the server to Claude Code

Run this once in your terminal:

```bash
claude mcp add Dataverse --transport sse http://localhost:8199/sse
```

Verify it was added:

```bash
claude mcp list
```

You should see `Dataverse` in the list with status `connected`.

### Step 3 — First sign-in (Claude Code)

In a Claude Code session, ask Claude to do something with Dataverse. Claude will call a
Dataverse tool, get an authentication error, and automatically call `Sign in to Dataverse`.
A sign-in URL will appear in the output — open it in your browser and sign in. Your browser
will show "Authentication complete" when done. Tell Claude you've signed in and it will
proceed with your request.

### Stopping the server

```bash
docker compose down
```

### Scope of MCP config in Claude Code

`claude mcp add` adds the server to your **user-level** config (`~/.claude/config.json`) by default,
making it available in all Claude Code sessions on your machine. To scope it to a single project only:

```bash
claude mcp add Dataverse --transport sse http://localhost:8199/sse --scope project
```

This writes to `.claude/config.json` in the current directory instead.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATAVERSE_URL` | Yes | — | Your environment URL, e.g. `https://yourorg.crm4.dynamics.com` |
| `CLIENT_ID` | Yes | — | Azure AD Application (client) ID from your Entra ID app registration |
| `TENANT_ID` | No | `common` | Azure AD tenant ID — find it in Azure Portal → Microsoft Entra ID → Overview |
| `AUTH_REDIRECT_PORT` | No | `5577` | Port for the interactive auth redirect server |

---

## Token Cache

Cache files are stored as JSON on the persistent Docker volume with `chmod 600` (owner
read/write only). The container runs as a non-root user, so only the container process
can access them. The Docker volume itself provides the isolation boundary.

**If a cache file is corrupted:** remove the Docker volume (`docker volume rm dataverse-mcp_dataverse-data`)
and restart. The server logs a clear error and continues with an empty cache rather than crashing.

---

## Example Conversations

**Create an appointment:**
> "Schedule a meeting with the subject 'Q4 Review' tomorrow at 2pm for 1 hour, assigned to me"

Claude will automatically: call `Get my identity` (cached) → call `Get table schema` for `appointment` (cached after first use) → call `Create record` with correctly formatted fields.

**Find contacts:**
> "Show me all contacts from Contoso"

Claude calls `List records` on the `contacts` table with an appropriate `$filter`.

**Fix stale schema:**
> "The appointment fields seem wrong, can you refresh the schema?"

Claude calls `Refresh schema cache` for the `appointment` table, then re-fetches on the next schema request.

---

## Security Notes

- Cache files are written with `chmod 600` (owner read/write only)
- The container runs as a non-root user (`mcpuser`)
- No client secrets are stored anywhere — interactive flow uses only public client credentials
- Never commit `.env` to git — it is listed in `.gitignore` by default in this repo

---

## Registering Your Own Azure AD App

Register an app in Azure Portal for your Dataverse MCP Server:

1. Go to **Azure Portal → Microsoft Entra ID → App registrations → New registration**
2. Name it anything (e.g. "Dataverse MCP Server")
3. Under **Authentication**, add platform **Mobile and desktop applications**
4. Add redirect URI: `http://localhost:5577` (or your custom `AUTH_REDIRECT_PORT`)
5. Under **API permissions**, add **Dynamics CRM → user_impersonation** (delegated)
6. Copy the **Application (client) ID** into your `.env` as `CLIENT_ID`
7. Set `TENANT_ID` to your specific tenant ID for tighter security (avoids `common`)

---

## Troubleshooting

**"No valid token found"** — The refresh token has expired
(after 90 days of inactivity). Sign in again when prompted.

**Claude Code: `claude mcp list` shows server as disconnected** — The Docker container is
not running. Run `docker compose up -d` (with the SSE override) before starting Claude Code.

**"attribute does not exist" errors when creating records** — The schema cache may be serving
stale data. Ask Claude to call `Refresh schema cache` for the affected table, then retry.

**Port 8199 already in use (Claude Code)** — Change the port in `docker-compose.sse.yml`
and update the `claude mcp add` URL accordingly.

**Port 5577 refused during sign-in (macOS)** — On macOS, `docker compose run` does not publish container ports by default. Add `"--service-ports"` to the `args` array in your `claude_desktop_config.json` (as shown in the configuration example above). If port 5577 is already allocated from a previous failed attempt, run `docker compose down` and remove stale containers before retrying.
