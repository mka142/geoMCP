# Connecting MCP clients

geomcp is plain MCP: any client that speaks stdio or Streamable HTTP works.
Local mode needs data-source credentials in the repository's `.env` (run `uv run geomcp setup` once); remote mode keeps them on the server.
Replace `/path/to/PFR` with the repository path, and `gmk_...` with a key from `geomcp-admin keys create`.

## Local (stdio): the engine runs on your machine

Claude Code:

```bash
claude mcp add geomcp -- uv --directory /path/to/PFR run geomcp serve --stdio
```

Claude app (desktop) and other clients with an `mcpServers` JSON config (Cursor, Windsurf, Cline, ...):

```json
{
  "mcpServers": {
    "geomcp": {
      "command": "uv",
      "args": ["--directory", "/path/to/PFR", "run", "geomcp", "serve", "--stdio"]
    }
  }
}
```

Without a Python toolchain, use the Docker image instead:

```json
{
  "mcpServers": {
    "geomcp": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "--env-file", "/path/to/PFR/.env", "-v", "geomcp-data:/data", "geomcp:latest", "geomcp", "serve", "--stdio"]
    }
  }
}
```

## Remote (Streamable HTTP): the shared VPS server

Claude Desktop and claude.ai (when the server has Google sign-in, see [deploy-vps.md](../deploy-vps.md)): Settings, Connectors, "Add custom connector", address `https://geo.example.org/mcp`.
Click "Connect" and sign in with the Google account on the team's list; no key and no JSON needed.
The connector then also works in the Claude mobile app.

Claude Code:

```bash
claude mcp add --transport http geomcp https://geo.example.org/mcp --header "Authorization: Bearer gmk_..."
```

Clients with an `mcpServers` JSON config that support remote servers and headers:

```json
{
  "mcpServers": {
    "geomcp": {
      "url": "https://geo.example.org/mcp",
      "headers": {"Authorization": "Bearer gmk_..."}
    }
  }
}
```

Debugging with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv --directory /path/to/PFR run geomcp serve --stdio
```

## Onboarding a new user

geomcp ships an `onboarding` prompt: a short guide in Polish to what you can ask, where the answers come from, the limits, and three starter questions.
It is built from the live data catalogue, so it stays current.

- Claude Code: type `/mcp__geomcp__onboarding` (optionally followed by a place, e.g. `/mcp__geomcp__onboarding gmina Sobótka`).
- Claude Desktop: click "+" in the message box, choose geomcp, then `onboarding`.
- Any client: ask "Zrób mi onboarding do geomcp".

The same menu holds the report prompts `site_profile`, `risk_profile` and `investment_profile`.
