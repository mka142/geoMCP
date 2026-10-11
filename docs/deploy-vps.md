# Deploying geomcp on a VPS with Docker

This guide runs geomcp as one Docker container on a Linux VPS, behind an HTTPS reverse proxy.
It assumes your own nginx terminates TLS with certificates you manage, and forwards plain HTTP to geomcp.
If nginx itself runs in Docker on a shared network, section 5b shows the matching compose file.
If you have no proxy, section 9 shows the built-in Caddy alternative.

## 1. What you need

- A Linux VPS with 2 vCPU, 4 GB RAM and 20 GB disk (for example Ubuntu 24.04).
  The cache grows with use: roughly 1 MB per area and decade of data.
- Docker Engine with the Compose plugin (`docker compose version` should work).
- A DNS name for the server (for example `geo.example.org`) and nginx serving it over HTTPS.
- The free data accounts: Copernicus Data Space (a Sentinel Hub OAuth client) and the Climate Data Store (a personal access token, with the licence of `reanalysis-era5-land-timeseries` accepted once on its Download tab).

## 2. Copy the code to the server

From your machine, copy the repository without local caches:

```bash
rsync -a --exclude .venv --exclude .env --exclude '__pycache__' ./ user@vps:/opt/geomcp/
```

## 3. Configure

On the server:

```bash
cd /opt/geomcp
cp .env.example .env
chmod 600 .env
nano .env
```

Set at least:

```
GEOMCP_CDSE_CLIENT_ID=...
GEOMCP_CDSE_CLIENT_SECRET=...
GEOMCP_CDS_KEY=...
GEOMCP_PUBLIC_URL=https://geo.example.org
GEOMCP_BIND=127.0.0.1:8000
```

`GEOMCP_PUBLIC_URL` is the address users reach; download links are built from it.
`GEOMCP_BIND` is where the container listens on the host: keep `127.0.0.1:8000` when nginx runs on the same machine.
If nginx runs on another machine, use the VPS's private address (for example `10.0.0.5:8000`) and allow only the proxy through the firewall.

## 4. Start

```bash
docker compose up -d --build
docker compose exec geomcp geomcp check      # one live call per data source: expect two OK lines
curl -s http://127.0.0.1:8000/healthz         # {"status":"ok",...}
```

## 5. Connect nginx

Add the `location` block from [nginx/geomcp.conf](nginx/geomcp.conf) to your HTTPS `server` block, then:

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -s https://geo.example.org/healthz                                        # {"status":"ok",...}
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://geo.example.org/mcp   # 401: a key is required
```

The important settings are `proxy_buffering off`, `proxy_http_version 1.1` and long read timeouts, because MCP streams its responses.

## 5b. nginx in Docker on a shared network

If nginx runs as a container that owns ports 80 and 443 and reaches apps over a Docker network (for example `net`), use `compose.proxy.yaml` instead of `compose.yaml`.
It publishes no host port and joins geomcp to that network under the name `geomcp`, so `GEOMCP_BIND` is not used.
Set the network name in `.env` when it is not `net`:

```
GEOMCP_PROXY_NETWORK=net
```

Start it, and test from inside the network:

```bash
docker compose -f compose.proxy.yaml up -d --build
docker compose -f compose.proxy.yaml exec geomcp geomcp check
docker run --rm --network net curlimages/curl -s http://geomcp:8000/healthz   # {"status":"ok",...}
```

Add the `location` block from [nginx/geomcp-docker.conf](nginx/geomcp-docker.conf) to your HTTPS `server` block and reload nginx (for example `docker exec nginx nginx -s reload`).
It looks geomcp up through Docker's DNS on each request, so nginx starts even while geomcp is down.
Then run the two `curl` checks from section 5.

With this file, add `-f compose.proxy.yaml` to every `docker compose` command in this guide (keys, stats, logs, update).
The data volume is the same `<folder>_geomcp-data` either way.

## 6. Give people access

Create one key per person; each key is shown once:

```bash
docker compose exec geomcp geomcp-admin keys create --name anna
docker compose exec geomcp geomcp-admin keys list
docker compose exec geomcp geomcp-admin keys revoke <id>
```

Each person connects their client to `https://geo.example.org/mcp` with the header `Authorization: Bearer <their key>`.
For Claude Code:

```bash
claude mcp add --transport http geomcp https://geo.example.org/mcp --header "Authorization: Bearer gmk_..."
```

Other clients are described in [clients/README.md](clients/README.md).

### Google sign-in (optional): connect with just the address

Claude Desktop and claude.ai add remote servers as "custom connectors", which take an address and OAuth but no API key.
With Google sign-in, people paste `https://geo.example.org/mcp`, sign in with Google, and are in.
API keys keep working next to it.

1. In [Google Cloud Console](https://console.cloud.google.com/) create or pick a project, then open "Google Auth Platform".
2. Branding: an app name (for example "geomcp") and a support email.
3. Audience: "Internal" if everyone has a Google Workspace account in your domain; otherwise "External", and add each person under test users.
4. Clients, "Create client": type "Web application", authorized redirect URI `https://geo.example.org/auth/callback`.
   Copy the client id and secret.
5. Add to `.env` and restart (`docker compose up -d`):

```
GEOMCP_GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GEOMCP_GOOGLE_CLIENT_SECRET=...
GEOMCP_ALLOWED_EMAILS=@example.com, someone@gmail.com
```

Only verified emails on `GEOMCP_ALLOWED_EMAILS` get in: whole addresses, or `@domain` for everyone in a domain.
The server refuses to start with Google sign-in but no allowlist, because any Google account could sign in otherwise.
`geomcp-admin stats` shows Google users by email.
Sign-in state lives in the data volume (`/data/fastmcp`), so restarts do not log people out.
To remove someone, take them off the list and restart.
Sign-in only returns to Claude's own callbacks (claude.ai, Claude Desktop, mobile) and to local ports (Claude Code); another client, such as Cursor, needs its callback added to `GEOMCP_OAUTH_REDIRECT_URIS`.

## 7. See who uses what

```bash
docker compose exec geomcp geomcp-admin stats            # last 30 days: calls, errors, speed, quota per person and tool
docker compose exec geomcp geomcp-admin stats --days 7
```

`/metrics` serves the same counters in Prometheus format and needs an API key (create one named `prometheus`).
Logs are JSON lines: `docker compose logs -f geomcp`.

## 8. Update, back up, restore

Update after copying new code to the server:

```bash
docker compose up -d --build
```

All state (API keys, jobs, usage, cache) lives in the `geomcp-data` volume.
Back it up while the service runs (SQLite is consistent at this level for a small team; stop the container first for a guaranteed snapshot):

```bash
docker run --rm -v geomcp_geomcp-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/geomcp-data-$(date +%F).tgz -C /data .
```

Restore on the same or a new server:

```bash
docker compose down
docker run --rm -v geomcp_geomcp-data:/data -v "$PWD":/backup alpine \
  sh -c 'rm -rf /data/* && tar xzf /backup/geomcp-data-YYYY-MM-DD.tgz -C /data'
docker compose up -d
```

The volume name is `<folder>_geomcp-data`; check it with `docker volume ls`.
The cache can always be rebuilt from the sources; the database holds the API keys.

## 9. Without your own proxy: Caddy

Set `GEOMCP_DOMAIN=geo.example.org` in `.env`, point its DNS at the VPS, open ports 80 and 443, and start with the Caddy profile:

```bash
docker compose --profile caddy up -d --build
```

Caddy obtains and renews the certificates itself.

## 10. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `geomcp check` fails for CDSE or CDS | Wrong credentials, or the CDS dataset licence not accepted |
| Clients hang or time out through nginx | `proxy_buffering` still on, or read timeouts too short |
| Download links point at `127.0.0.1` | `GEOMCP_PUBLIC_URL` not set |
| `list_data` shows `quota_low` | Over 80% of the monthly Sentinel Hub units used; cached answers still work |
| A source shows `source_busy` | The provider is overloaded; other sources keep working, try again later |
| Statistics answer "GUS BDL request limit reached" | The anonymous GUS quota (1,000 requests per 12 hours) is used up; set `GEOMCP_GUS_BDL_KEY` ([keys-registration.md](keys-registration.md)) |
