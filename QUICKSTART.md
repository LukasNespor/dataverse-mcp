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
        "compose",
        "-f", "/absolute/path/to/dataverse-mcp/docker-compose.yml",
        "run", "--rm", "-i", "--service-ports",
        "dataverse-mcp"
      ]
    }
  }
}
```

> **Important:** Replace the path with the absolute path to your cloned repo. If your config file already exists and contains other MCP servers, add the `"Dataverse": { ... }` entry inside the existing `"mcpServers"` object.

Fill in your values in `.env` (copy from `.env.example`). Restart Claude Desktop. The container (including Redis) starts automatically when Claude Desktop launches.

### Claude Code

Start the server with the SSE transport override:

```bash
docker compose -f docker-compose.yml -f docker-compose.sse.yml up -d
```

Register the server in Claude Code:

```bash
claude mcp add Dataverse --transport sse http://localhost:8199/sse
```

> **Important:** The containers must be running before you start Claude Code. If you start Claude Code first, it will fail to connect and you will need to restart Claude Code.

## 2. Sign in

Ask Claude anything that involves Dataverse/CRM, e.g.:

> "Show me my recent contacts in CRM"

Claude will detect that authentication is needed and show a Microsoft sign-in URL. Open it in your browser, sign in, and tell Claude you're done. That's it — you won't need to sign in again for 90 days.
