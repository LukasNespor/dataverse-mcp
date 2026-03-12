# Dataverse MCP Server

An MCP server that connects Claude to Microsoft Dataverse via the Dataverse Web API v9.2.
Designed for everyday users who work with Dataverse records — not for admin operations like
managing environments, solutions, or security roles.

Uses Entra ID On-Behalf-Of (OBO) flow for multi-user authentication. Each user signs in
through the MCP client's OAuth flow and receives their own Dataverse access token.

Uses Redis for shared cache and proposal storage. Destructive operations (delete) use a
two-step propose/confirm workflow with cryptographic confirmation tokens for safety.

---

## Available Tools

| Tool | Category | Cached | Description |
|---|---|---|---|
| `Get my identity` | READ | 24h TTL | Get the current user's GUID, display name, business unit, org ID, and timezone |
| `List tables` | READ | 24h TTL | List all business tables with LogicalName, DisplayName, and EntitySetName |
| `Get table schema` | READ | 1h TTL | Retrieve field definitions, types, and required fields per table |
| `Refresh schema cache` | READ | — | Force a fresh schema fetch; use if schema seems stale after customizations |
| `List records` | READ | — | Query records with OData $filter, $select, $orderby, and pagination |
| `Create record` | CREATE | — | Create a new record (always call `Get table schema` first) |
| `Update record` | UPDATE | — | Partially update an existing record via PATCH |
| `Delete record` | DESTRUCTIVE | — | Propose permanent deletion — does NOT delete immediately |
| `Confirm delete record` | DESTRUCTIVE | — | Execute a previously proposed deletion after user confirmation |

> **Two-step delete:** `Delete record` creates a time-limited proposal with a one-time confirmation token. The agent must show the impact summary to the user, get explicit confirmation, then call `Confirm delete record` with the token. Proposals expire after 2 minutes and cannot be reused.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose installed
- A Microsoft Dataverse / Power Platform environment URL
- An Azure AD app registration with a client secret (see [Azure AD App Registration](#azure-ad-app-registration))

---

## Setup

### 1. Clone and configure

```bash
git clone <repo-url>
cd dataverse-mcp
cp .env.example .env
```

Edit `.env` and set the required variables:

```env
DATAVERSE_URL=https://yourorg.crm4.dynamics.com
TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret
```

> **Important:** `TENANT_ID` must be the tenant GUID (e.g. `bb05be88-...`), not a domain name (e.g. `contoso.com`). Azure endpoints accept both forms, but the JWT `iss` claim always uses the GUID — a domain name causes issuer mismatch during token validation.

### 2. Build and start

```bash
docker compose up -d --build
```

This starts both the MCP server (port 8000) and Redis.

---

## Connecting to Claude Code

### Add the server

```bash
claude mcp add Dataverse --transport http http://localhost:8000/mcp
```

Verify it was added:

```bash
claude mcp list
```

You should see `Dataverse` in the list with status `connected`.

When Claude Code connects, the server redirects you to Entra ID sign-in. After authentication, the OBO exchange happens automatically and all Dataverse calls use your per-user token.

### Scope of MCP config

`claude mcp add` adds the server to your **user-level** config (`~/.claude/config.json`) by default,
making it available in all Claude Code sessions on your machine. To scope it to a single project only:

```bash
claude mcp add Dataverse --transport http http://localhost:8000/mcp --scope project
```

This writes to `.claude/config.json` in the current directory instead.

### Stopping the server

```bash
docker compose down
```

---

## Azure AD App Registration

Configure your app registration in **Azure Portal → Microsoft Entra ID → App registrations**:

1. Go to **App registrations → New registration**, name it (e.g. "Dataverse MCP Server")
2. **Authentication** → Add platform → **Web** → Redirect URI: `http://localhost:8000/auth/callback` (local) or `https://your-app.azurewebsites.net/auth/callback` (production)
3. **Expose an API** → Set Application ID URI (accept the default `api://<CLIENT_ID>`)
4. **Expose an API** → Add a scope → Name: `mcp-access`, Who can consent: Admins and users
5. **API permissions** → Add → **Dynamics CRM** → `user_impersonation` (delegated) → Grant admin consent
6. **Certificates & secrets** → New client secret → Copy the value to `.env` as `CLIENT_SECRET`
7. Copy the **Application (client) ID** into `.env` as `CLIENT_ID`
8. Set `TENANT_ID` to your tenant GUID

---

## Architecture

```
┌─────────────┐       HTTP        ┌──────────────────┐      OBO        ┌────────────┐
│ Claude Code  │ ◄───────────────► │  MCP Server      │ ◄─────────────► │ Entra ID   │
│ (MCP client) │   (OAuth 2.1)    │  (FastMCP +      │  token exchange │            │
└─────────────┘                   │   AzureProvider) │                 └────────────┘
                                  └──────┬───────────┘
                                         │                               ┌────────────┐
                                         ├── OData v9.2 ────────────────►│ Dataverse  │
                                         │   (per-user OBO token)        │ Web API    │
                                         │                               └────────────┘
                                         │ Redis protocol
                                         ▼
                                  ┌──────────────┐
                                  │    Redis      │
                                  │  (cache +     │
                                  │  proposals)   │
                                  └──────────────┘
```

The server acts as a confidential client. The MCP client authenticates the user via Entra ID OAuth, and the server exchanges the user's token for a Dataverse access token using the On-Behalf-Of (OBO) flow. Each user gets their own Dataverse token — the server never shares tokens across users. Cache keys for identity data are scoped per user via the `oid` claim from the JWT.

- **Cache** (Redis): WhoAmI identity (24h TTL, per-user), table schema (1h TTL, shared), table list (24h TTL, shared).
- **Proposals** (Redis): Two-step delete proposals with cryptographic tokens, automatic TTL expiry, and atomic replay protection via Lua CAS script.
- **Audit**: Structured JSON audit logging on every tool invocation. Destructive actions log proposal creation and confirmation separately with user context from the OBO token.
- **Input validation**: Table names and record GUIDs are validated before any API call.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATAVERSE_URL` | Yes | — | Your environment URL, e.g. `https://yourorg.crm4.dynamics.com` |
| `CLIENT_ID` | Yes | — | Azure AD Application (client) ID from your Entra ID app registration |
| `CLIENT_SECRET` | Yes | — | Azure AD Application (client) secret from your Entra ID app registration |
| `TENANT_ID` | No | `common` | Azure AD tenant ID (must be the GUID, not a domain name) |
| `REDIS_URL` | Yes | — | Redis connection string (set automatically in docker-compose, e.g. `redis://redis:6379/0`) |
| `MCP_BASE_URL` | No | `http://localhost:8000` | Public URL of the server (used for OAuth redirect) |
| `JWT_SIGNING_KEY` | No | — | Stable key for signing JWTs issued by the OAuth proxy. Must persist across restarts. Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `STORAGE_ENCRYPTION_KEY` | No | — | Fernet key for encrypting OAuth tokens at rest in Redis. Must persist across restarts. Generate: `python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `CONFIRM_TOKEN_TTL_SECONDS` | No | `120` | How long a delete proposal remains valid (seconds) |

---

## Redis

Redis is required and runs as a sidecar container via docker-compose. It stores:

- **App cache**: WhoAmI identity (per-user), table schema, and table list with native TTL expiry
- **Delete proposals**: Two-step confirmation tokens with automatic expiry
- **OAuth session state**: Client registrations, authorization codes, and issued tokens — encrypted at rest with Fernet when `STORAGE_ENCRYPTION_KEY` is set

To inspect cached data:

```bash
docker compose exec redis redis-cli
KEYS dataverse:*
```

Redis data persists across container restarts via the `redis-data` Docker volume.

---

## Example Conversations

**Create an appointment:**
> "Schedule a meeting with the subject 'Q4 Review' tomorrow at 2pm for 1 hour, assigned to me"

Claude will automatically: call `Get my identity` (cached) → call `Get table schema` for `appointment` (cached after first use) → call `Create record` with correctly formatted fields.

**Find contacts:**
> "Show me all contacts from Contoso"

Claude calls `List records` on the `contacts` table with an appropriate `$filter`.

**Delete a record:**
> "Delete the lead with ID abc123..."

Claude calls `Delete record` (creates a proposal) → shows the impact summary → asks for confirmation → calls `Confirm delete record` with the token.

**Fix stale schema:**
> "The appointment fields seem wrong, can you refresh the schema?"

Claude calls `Refresh schema cache` for the `appointment` table, then re-fetches on the next schema request.

---

## Security Notes

- Two-step propose/confirm workflow for all destructive operations
- Confirmation tokens are SHA-256 hashed — plaintext is never stored
- Atomic replay protection via Redis Lua CAS script prevents double-execution
- Input validation on all table names and record GUIDs (prevents injection)
- Structured audit logging on every tool invocation with user context
- The container runs as a non-root user (`mcpuser`)
- The client secret is passed only via environment variable — never stored in code or logs
- OAuth session state (tokens, codes) is encrypted at rest in Redis using Fernet symmetric encryption
- Per-user OBO tokens ensure users can only access Dataverse data they are authorized for
- Never commit `.env` to git — it is listed in `.gitignore` by default in this repo

---

## Production Deployment

For Azure deployment (e.g. Azure Container Apps, App Service), set `MCP_BASE_URL` to the public URL of the server and configure the redirect URI in the app registration accordingly. The server listens on port 8000 with `stateless_http=True`, so it can run behind a load balancer with multiple replicas sharing the same Redis instance.

---

## Running Tests

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -v
```

Tests use `fakeredis` — no running Redis instance required.

---

## Troubleshooting

**Claude Code: `claude mcp list` shows server as disconnected** — The Docker containers are
not running. Run `docker compose up -d` before starting Claude Code.

**"attribute does not exist" errors when creating records** — The schema cache may be serving
stale data. Ask Claude to call `Refresh schema cache` for the affected table, then retry.

**"Redirect URI not registered"** — The redirect URI in your Entra ID app registration must exactly match `{MCP_BASE_URL}/auth/callback`. For local testing this is `http://localhost:8000/auth/callback`. Make sure you added it under the **Web** platform.

**"AADSTS65001" or consent errors** — Admin consent has not been granted for the Dynamics CRM `user_impersonation` permission. Go to Azure Portal → App registrations → API permissions → Grant admin consent.

**"accessTokenAcceptedVersion" errors** — The server accepts both v1.0 and v2.0 Azure tokens automatically. If you still see token version issues, verify the app registration's "Expose an API" section has the Application ID URI set.

**Redis connection error on startup** — The MCP server requires Redis. Ensure you're using `docker compose` (which starts both services) rather than running the container directly. Check `docker compose ps` to verify the Redis container is healthy.
