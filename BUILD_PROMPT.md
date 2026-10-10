# Build geomcp: an MCP server for fast geodata answers

## 1. Role and goal

You are a senior geospatial backend engineer.
Build `geomcp`: a Python core library plus a model-agnostic MCP server that lets non-specialists (executives, researchers) ask GIS questions through any MCP client (Claude Code, the Claude app, other LLM clients) and get fast, sourced numbers and occasional images.
Typical question: "How did vegetation, temperature and air quality change in gmina X over the last 10 years?"
Typical AOI: up to 25 km²; typical range: 1-10 years.

Before writing code, read `eo-gis-data-access-guide.md` in full; it is the source of truth for sources, auth, licences and gotchas.
Then read `TOOL_EXAMPLES.md`: it is the target tool contract, and its examples become contract-test fixtures.
Before writing any adapter, open its doc links (guide §12 and §4); never code from memory.
Items tagged `[U]` are unverified: give each one you depend on a passing `live` smoke test before code relies on it.
Ask questions only when truly blocked; otherwise choose the conventional option, record it in `docs/adr/`, and continue.
Work in small, testable steps, and stop after every milestone (section 8).

Fixed decisions (do not reopen):
- Numbers first: tools return compact statistics with provenance; images are small PNG snapshots.
  Full rasters (Zarr, COG, STAC) are a later phase.
- Prefer hosted engines that compute aggregates server-side; download pixels only when no engine covers the request.
- Use all available data, including restricted sources.
  Every value carries `licence`, `attribution` and `commercial_safe`; answers using restricted data say so in a `notices` field.
- Gaps and failures are explicit, never silent; every value has a `status` (`final`, `preliminary`, `missing`).
- Times are UTC; periods are `[start, end)` and labelled by their start; weekly periods are ISO weeks.
- Model-agnostic: plain MCP, no client-specific features required.

## 2. Architecture

### 2.1 Packages (one repo, uv workspace)

- `geomcp-core`: area resolution, source registry, router, adapters, harmoniser, indicators, cache, provenance.
  No web, MCP or database dependencies; usable directly from Python and notebooks.
- `geomcp-server`: the MCP server (FastMCP), job store, auth and a few HTTP routes, only orchestrating core.

Every capability is a core function first; MCP tools are thin, hand-written wrappers.
Do not auto-generate tools from an OpenAPI document: LLM tools need task-level design and careful descriptions.

### 2.2 Request flow

`resolve_area -> router -> adapters (parallel) -> harmoniser -> summariser -> tool result`
- **Router**: picks a backend per variable by access pattern, in this order: `server_stats` (aggregates computed by the provider), `timeseries` (time-chunked store or point API), `pixels` (our own download, later phase).
  It applies `commercial_safe_only` only when the caller sets it (default false).
  It estimates cost (seconds, provider units) from metadata before calling anything.
- **Adapters**: implement guide §2.2 (`search`, `load`, `aggregate`) or a single `stats(area, variable, periods)` for `server_stats` sources, plus a static `SourceInfo` (variables with CF names and units, native resolution, coverage, latency, licence, attribution, `commercial_safe`, auth, access pattern, quota and rate-limit policy).
  Discover adapters via Python entry points (`geomcp.adapters`): a new source is one module, one test file and cassettes, with no edits to core.
- **Harmoniser**: converts units (K to °C, m to mm, ...), aligns periods (guide §6), computes derived indicators (guide §5) when a source returns primitives, and marks status and gaps.
- **Summariser**: turns long series into a compact answer: per-variable trend (Theil-Sen slope per year), min/max with dates, latest value, gap count, and at most `max_rows` rows (default 60), with a download link for the full table.

### 2.3 Cache and state

- Cache every adapter result under `sha1(source, collection_version, area_hash, variable, period_grid)`; identical questions from different users must not spend quota twice.
- Storage goes through `fsspec` (`file://` default, S3-compatible optional).
- SQLite (WAL) via SQLAlchemy 2 and Alembic stores the cache index, jobs, API keys and quota usage.
  Use portable SQL so Postgres is a later URL change.
- Per-source rate limits and quotas (CDSE Sentinel Hub monthly requests and processing units, GIOŚ 2 requests/min, Nominatim 1 request/s) live in one limiter module and are counted in SQLite.

### 2.4 Provenance

Every returned value or series carries: `variable`, `units`, `source`, `backend`, `collection`, `method` (one line, e.g. "S2 L2A, SCL cloud mask, mean over valid pixels"), `status`, `valid_fraction`, `licence`, `attribution`, `commercial_safe`, `retrieved_at`.

## 3. MCP interface

Use FastMCP (the `fastmcp` package; check its current docs).
Each tool has a precise description, typed parameters with examples, an `outputSchema`, and returns both `structuredContent` and a short text summary, so clients that ignore structured output still work.
Keep the tool set small:

| Tool | Purpose |
|---|---|
| `resolve_area(query, buffer_m?, shape?)` | Place name, address, Polish gmina/powiat (TERYT), parcel ID (GUGiK ULDK) or `lat,lon` (buffered as a circle or square) to an area id, GeoJSON, area in km², and admin codes. Sources: Nominatim (follow its usage policy), GUGiK PRG boundaries, ULDK. Returns candidates when ambiguous. |
| `list_data(topic?, area_id?)` | Available variables grouped by topic (vegetation, water, weather, air quality, terrain, land cover, population, infrastructure), with coverage, latency, resolution, licence, `commercial_safe`, and live health (`ok`, `degraded`, `unconfigured`, `quota_low`). |
| `get_timeseries(area_id, variables, start, end, cadence?, stats?)` | Compact series plus summary per variable. Cadence `P1W`, `P1M`, `P3M` (quarterly, default for ranges above 2 years) or `P1Y`. |
| `get_summary(area_id, variables, date_or_period?)` | One-shot facts: land-cover shares, elevation and slope statistics, population, building and road counts, municipal statistics, latest air quality. |
| `compare_areas(area_ids, variable, start, end, cadence?)` | The same variable across up to 10 areas, ranked, with the same provenance. |
| `get_snapshot(area_id, layer, date?, size_px?)` | A PNG (MCP image content, at most 1024 px) for `true_color`, `false_color`, `ndvi`, `ndwi`, `sar_vv`, `elevation` or `landcover`, plus the scene date, cloud cover and a legend. |
| `get_job(job_id)` | Status and result of a long-running call. |

Long calls: if the router's estimate exceeds `GEOMCP_SYNC_BUDGET_S` (default 45 s), the tool returns `{job_id, eta_s}` instead of blocking, and the client polls `get_job`.
Also send MCP progress notifications where the client supports them.
Every result includes a signed `download_url` for the full table (CSV and Parquet), served by an HTTP route in remote mode or written to a local path in stdio mode.
Errors are structured (`code`, `message`, `hint`), for example `area_too_large` with the limit, `source_unconfigured` with the env var to set, `quota_exhausted` with the reset date.
Also expose a resource `geomcp://sources` (the catalogue in Markdown) and one prompt `site_profile(area)` that chains the tools into a standard briefing.

## 4. Transports, deployment and auth

- **Local (stdio)**: `uvx geomcp` or `geomcp serve --stdio`; works with Claude Code, the Claude app (desktop config) and other MCP clients; credentials from env or a `.env` file; no auth.
- **Remote (Streamable HTTP)**: `geomcp serve --http` on port 8000 at `/mcp`.
- **Docker**: one small image (multi-stage, non-root, `python:3.12-slim` base) with a `/data` volume and an env file.
  `compose.yaml` runs the server plus Caddy for automatic HTTPS, so a VPS deployment is `cp .env.example .env`, edit, `docker compose up -d`.
  Add `/healthz` and a Prometheus `/metrics` route.
- **Auth for HTTP**: do not build custom auth.
  Use FastMCP's built-in bearer-token verification with API keys stored hashed in SQLite, and provide `geomcp-admin keys create|list|revoke`.
  Most MCP clients can send an `Authorization` header; document the config for Claude Code and at least one other client.
  Keep the door open for FastMCP's OAuth providers, but do not implement OAuth now.
- Limits in HTTP mode: per-key rate limit, AOI at most 1,000 km² for `server_stats` sources and at most 25 km² for any pixel path.

## 5. Sources and phases

Choices come from live benchmarks over 10 km² of Wrocław (October 2026): server-side statistics answer in seconds to minutes; our own pixel downloads take 30 min to hours for 10 years.

- **Phase 1 (MVP)**:
  - CDSE Sentinel Hub Statistical API: Sentinel-2 L2A indices (NDVI, NDWI, MNDWI, NDMI, NBR, NDBI) with SCL cloud masking in the evalscript, Sentinel-1 backscatter, Sentinel-5P (NO2, O3, CO, ...), Copernicus DEM statistics (guide §7.6).
  - CDSE Sentinel Hub Process API: PNG snapshots for `get_snapshot`.
  - CDS time-series datasets `reanalysis-era5-single-levels-timeseries` and `reanalysis-era5-land-timeseries` for weather (temperature, precipitation, wind, radiation, soil moisture, VPD); Earthmover Icechunk `single/temporal` as fallback (it lags about 6 months).
    Do not use the ARCO store of guide §7.7 for long series: its chunks are 1 hour x the whole globe.
  - Area resolution: Nominatim, GUGiK PRG, GUGiK ULDK.
- **Phase 2**:
  - Polish sources: GIOŚ air quality (nearest stations plus archives), IMGW synop and archives (restricted: `commercial_safe=false`), GUS BDL municipal statistics via TERYT, GUGiK NMT 1 m height statistics (guide §4.13, §7.12-7.15).
  - OSM via Overpass (buildings, roads, POI counts; ODbL), land cover (ESA WorldCover), population (GHSL or WorldPop).
  - Google Earth Engine (restricted): Dynamic World, Satellite Embedding, and a general `reduceRegion` fallback, enabled when credentials exist (guide §7.9).
  - CAMS through ADS as a job-based source (queue latency).
- **Phase 3 (if needed)**: own pixel engine (odc-stac on Earth Search, Planetary Computer fallback for missing Sentinel-2 years), Landsat, IMERG via OPeNDAP [U], openEO, raster outputs (COG/Zarr/STAC with signed links), a REST API over the same core.

Accounts needed (all free): CDSE, CDS/ADS, NASA Earthdata (phase 3), Google Cloud for GEE (phase 2).
A source without credentials reports `unconfigured` in `list_data`; it never fails the whole call.
Sentinel Hub on CDSE has monthly free quotas (guide §3.1): track usage per call, show `quota_low` early, and rely on the cache.

## 6. Engineering rules

- Python 3.12 and `uv` with a committed lockfile and exact pins; `ruff`, `pyright` (strict on core), `pre-commit`.
- Secrets only from env (`pydantic-settings`, `.env.example`), never logged.
- `httpx` with explicit timeouts; retry only retryable errors, with backoff and jitter (`stamina` or `tenacity`).
- `structlog` JSON logs with `call_id`, `tool`, `source` bound; log provider units spent per call.
- Evalscripts for Sentinel Hub live as versioned files under `adapters/evalscripts/`, and their version is part of the cache key.
- Every guide §10 gotcha that touches an implemented source becomes a named test (`test_gotcha_05_era5land_accumulation`, ...); later-phase items are `xfail` with a reason.

## 7. Testing

- Unit tests with recorded HTTP (`pytest-recording`/VCR) for every adapter.
- MCP contract tests with FastMCP's in-memory client: every tool in `TOOL_EXAMPLES.md` returns schema-valid `structuredContent` and a text summary.
- Golden AOI: 10 km² square centred on Wrocław Rynek (51.110 N, 17.032 E).
  Sanity checks: NDVI in [-1, 1]; precipitation at least 0 mm; 2 m temperature plausible for the season in °C; elevation 100-160 m; every value has units, licence and status; every gap listed.
- Cross-check (`live`): Sentinel Hub NDVI means for 5 golden dates match a local odc-stac computation (guide §7.1) within 0.03.
- Performance (`live`): 10 years of quarterly NDVI plus monthly weather for the golden AOI returns within 60 s with a cold cache and within 2 s with a warm one.
- Client tests: run the server with MCP Inspector, Claude Code and at least one non-Anthropic MCP client; record the configs in the README.
- `live` tests skip without credentials, run nightly in CI, and feed `list_data` health.
- CI (GitHub Actions): lint, type check, unit and contract tests, Docker build plus a smoke test against cassettes, nightly live job.

## 8. Delivery plan

Each milestone ends with a runnable demo in an updated README (example prompts plus the tool calls they trigger).
Then report what you built, what you verified (with command output) and the open issues, and stop.

- **M0 Skeleton**: workspace, tooling, CI, Dockerfile, compose with Caddy, FastMCP server with a synthetic source, stdio and HTTP transports, auth with hashed keys.
  Done when the demo source answers in Claude Code over stdio and over HTTP from the container.
- **M1 MVP sources**: area resolution, Sentinel Hub Statistical and Process adapters, CDS time series with Earthmover fallback, router, cache, quota tracking, summariser, all seven tools.
  Done when the golden questions in `TOOL_EXAMPLES.md` work end to end and the cross-check and performance tests pass.
- **M2 Phase 2 sources**: Polish sources, OSM, land cover, population, GEE, CAMS jobs, the `site_profile` prompt.
- **M3 Hardening**: limits, error catalogue, metrics, a VPS deployment guide, and a backup of the SQLite file and cache.
- **M4+**: phase 3 items on request.

## 9. Repository structure

```
packages/core/src/geomcp_core/{areas,registry,router,harmonise,indicators,summarise,cache,provenance,adapters/}
packages/server/src/geomcp_server/{tools/,auth/,jobs/,routes/}
tests/{unit,contract,golden,live,cassettes}
docs/{adr/,clients/}  Dockerfile  compose.yaml  Caddyfile  .env.example  TOOL_EXAMPLES.md
```

## 10. Non-goals for now

A UI, user management beyond API keys, OAuth, billing, ML models, Postgres or Redis, Kubernetes, raster file outputs and a REST/OGC API (phase 3), and any client-specific MCP features.
