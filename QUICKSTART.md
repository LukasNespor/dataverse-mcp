# Quick Start

Get the Dataverse (CRM) MCP server running in under 5 minutes.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) installed
- An Azure AD app registration with **Dynamics CRM > user_impersonation** permission and redirect URI `http://localhost:5577` (see [README](README.md#registering-your-own-azure-ad-app) for details)
- A Microsoft account with access to a Dataverse / Power Platform environment

## 1. Connect to Claude

### Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "Dataverse": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "DATAVERSE_URL=https://yourorg.crm4.dynamics.com",
        "-e", "CLIENT_ID=your-azure-app-client-id",
        "-e", "TENANT_ID=your-azure-tenant-id",
        "-v", "dataverse-data:/data",
        "-p", "5577:5577",
        "112567/dataverse-mcp"
      ]
    }
  }
}
```

> **Important:** If your config file already exists and contains other MCP servers, do not replace the entire file. Add the `"Dataverse": { ... }` entry inside the existing `"mcpServers"` object.

Replace placeholder values. Restart Claude Desktop. The container starts automatically when Claude Desktop launches and is removed when the app closes.

### Claude Code

Create a `docker-compose.yml` — fill in your values and it's ready to go:

```yaml
services:
  dataverse-mcp:
    image: 112567/dataverse-mcp
    container_name: dataverse-mcp
    environment:
      - DATAVERSE_URL=https://yourorg.crm4.dynamics.com
      - CLIENT_ID=your-azure-app-client-id
      - TENANT_ID=your-azure-tenant-id
      - MCP_TRANSPORT=sse
      - MCP_HOST=0.0.0.0
    ports:
      - "5577:5577"
      - "8199:8000"
    volumes:
      - dataverse-data:/data
    restart: unless-stopped

volumes:
  dataverse-data:
```

Start the server:

```bash
docker compose up -d
```

Register the server in Claude Code:

```bash
claude mcp add Dataverse --transport sse http://localhost:8199/sse
```

> **Important:** The container must be running before you start Claude Code. If you start Claude Code first, it will fail to connect to the MCP server and you will need to restart Claude Code.

## 2. Sign in

Ask Claude anything that involves Dataverse/CRM, e.g.:

> "Show me my recent contacts in CRM"

Claude will detect that authentication is needed and show a Microsoft sign-in URL. Open it in your browser, sign in, and tell Claude you're done. That's it — you won't need to sign in again for 90 days.
