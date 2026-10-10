# ADR 0009: Google sign-in next to API keys

Status: accepted, October 2026.

## Context

Claude Desktop and claude.ai add remote MCP servers as custom connectors: an address plus OAuth, with no way to send an API key header.
API keys (ADR 0002) therefore reach only Claude Code and clients with a JSON config, and co-founders should not need either.

## Decision

- Optional Google sign-in through FastMCP's `GoogleProvider`, an OAuth proxy that offers clients dynamic registration and hands the login to Google.
  It is enabled by `GEOMCP_GOOGLE_CLIENT_ID` and `GEOMCP_GOOGLE_CLIENT_SECRET`.
- Access is limited to verified emails on `GEOMCP_ALLOWED_EMAILS` (whole addresses or `@domain`), checked on every token, so removing someone takes effect at the next restart.
  Without the list the server refuses to start, because any Google account could sign in otherwise.
- `MultiAuth` puts Google first and API keys second, so Claude Code, scripts and Prometheus keep their keys.
- The Google user's email replaces the client id in logs and `geomcp-admin stats`.
- OAuth client registrations are stored, encrypted, in the data volume, so restarts do not log people out.
- FastMCP's consent page stays on: it stops another site from silently reusing a registered client.
- Clients may register only redirect URIs on `GEOMCP_OAUTH_REDIRECT_URIS`: by default Claude's callback (`claude.ai`, and `claude.com`, which Anthropic announced as its successor) and loopback ports for Claude Code, so a phishing client cannot register a callback of its own.

## Consequences

Every MCP request with a Google token costs two calls to Google (token info and profile); this is fine for a small team.
A person who signs in but is not on the list gets "unauthorized" from the client rather than a friendly page.
