# geomcp

A model-agnostic MCP server for fast, sourced geodata answers.
Ask any MCP client (Claude Code, the Claude app, Cursor, ...) questions such as "how did vegetation, temperature and air quality change in gmina X over 10 years?", and get compact numbers with units, status, licence, attribution and method.

Status: **M4 (Poland) in progress**, real data.
Sentinel-2, Sentinel-1, Sentinel-5P and the Copernicus DEM come through Sentinel Hub on the Copernicus Data Space (CDSE).
Weather comes from ERA5-Land through the Climate Data Store (CDS).
Land cover comes from ESA WorldCover, infrastructure counts from OpenStreetMap, municipal statistics from GUS, current station air quality from GIOŚ, and 1 m terrain from GUGiK NMT.
Thousands of official indicators come from the GUS Local Data Bank (BDL) and public-services monitoring (SMUP).
Hazards and constraints come from ISOK flood maps, the GDOŚ nature register, PIG-PIB (landslides, mining, EGMS ground motion) and IMGW warnings.
Places come from OpenStreetMap (Nominatim) and Polish parcels and gminas from GUGiK ULDK.
Full result tables are downloadable as CSV and Parquet.

## Tools

| Tool | What it does |
|---|---|
| `resolve_area(query, buffer_m, shape)` | A place name or address, a gmina, a Polish parcel id or `lat,lon` to an `area_id`. Ambiguous names return candidates. |
| `list_data(topic?)` | Variables with units, coverage, licence, `commercial_safe`, health, and this month's Sentinel Hub usage. |
| `get_timeseries(area_id, variables, start, end, cadence?)` | Per-period values (week, month, quarter, year) with a seasonal trend and p-value, gaps and notices. |
| `get_summary(area_id, variables)` | Terrain (elevation, slope) plus recent values (last 30 days of weather, 60 days of satellite data). |
| `compare_areas(area_ids, variable, start, end)` | Ranks 2-10 areas by the mean (or total) over a period. |
| `get_snapshot(area_id, layer, date?)` | A north-up PNG: `true_color`, `false_color`, `ndvi`, `ndwi`, `sar_vv`, `elevation`, or `orthophoto` (GUGiK aerial photo, Poland, up to 25 cm). |
| `describe_data(topic?, area_id?)` | The catalogue in plain language: what each dataset measures, where and how far back, limits, cost, example questions; with `area_id`, what is available there. |
| `find_statistics(query)` | Search official Polish indicators (GUS BDL and SMUP) by Polish words; returns ids such as `bdl:72305` or `smup:10`. |
| `get_statistics(area_id, indicator_ids, years?)` | Indicator values for the area's gmina, with its powiat, voivodeship and Poland for comparison. |
| `get_constraints(area_id)` | Shares of the area in flood zones (10%, 1%, 0.2%, embankment failure) and protected nature, landslides, mining terrains and deposits, ground motion in mm/year, and current IMGW warnings (Poland). |
| `get_prices(area_id, since_year?)` | Real-estate transaction prices from the national register (RCN): yearly medians for apartments (PLN/m², new and resale), houses and plots (Poland, up to 25 km²). |
| `get_zoning(area_id)` | Local zoning plan (MPZP) designations, plan name, adoption date and link, sampled at points (Poland, up to 5 km²). |
| `get_traffic(area_id)` | Vehicles per day and heavy-vehicle share on national roads crossing the area (GDDKiA survey 2025, with the change since 2020/21). |
| `lookup_business(identifier)` | A Polish company by KRS, NIP or REGON: VAT status, address, capital, activities (PKD), board roles, owners, liquidation or bankruptcy entries. |
| `find_businesses(name?, area_id?, town?, pkd?)` | Sole traders (CEIDG) by name, gmina, town and PKD: how many are active, and up to 5 of them. |
| `get_job(job_id)` | Result of a call that took longer than the sync budget (45 s); it waits up to 20 s per call. |

Prompts: `onboarding(place?)`, a short guide in Polish for new users (what to ask, where answers come from, limits, three starter questions), built from the live catalogue; `site_profile(place, years)`, a standard sourced briefing that chains the tools (terrain, land cover, infrastructure, population, trends, air quality, image); `risk_profile(place, years)`, flooding, nature protection, ground, heat and heavy-rain trends, warnings; `investment_profile(place)`, zoning, prices, traffic, services, risks and an aerial photo.

Variables over time (`get_timeseries`): `ndvi`, `ndmi`, `nbr`, `ndwi`, `mndwi`, `ndbi` (Sentinel-2); `s1_vv`, `s1_vh` (Sentinel-1, dB); `no2_column` (Sentinel-5P); `t2m`, `t2m_max`, `t2m_min`, `precipitation`, `wind_speed`, `solar_radiation`, `soil_moisture`, `vpd` (ERA5-Land); day counts `hot_days`, `summer_days`, `tropical_nights`, `frost_days`, `heavy_rain_days`, `dry_days` (from ERA5-Land daily values).

Facts (`get_summary`): `elevation`, `slope` (Copernicus DEM, worldwide); `elevation_ground`, `slope_ground` (GUGiK NMT, Poland); `landcover` (ESA WorldCover); `buildings`, `schools`, `healthcare`, `shops`, `transit_stops`, `road_length` (OpenStreetMap); `population`, `population_density`, `net_migration`, `natural_increase`, `dwellings_completed`, `new_businesses`, `budget_income_per_capita` (GUS, for the gmina); `pm10_station`, `pm25_station`, `no2_station`, `o3_station` (GIOŚ, last 24 hours); `pm10_history`, `pm25_history`, `no2_history` (GIOŚ, verified annual means since 2000); `building_functions` (GUGiK BDOT10k, Poland); `population_grid` (JRC GHSL, residents inside the area 2000-2025, worldwide); `solar_potential` (JRC PVGIS); `landcover_history` (CORINE 1990-2018, Europe); `climate_projection` (Open-Meteo CMIP6, 2031-2050 against 1991-2010, non-commercial only).
Real examples of every tool are in [TOOL_EXAMPLES.md](TOOL_EXAMPLES.md), generated from live calls.

## Quick start (local)

Requires [uv](https://docs.astral.sh/uv/) and two free accounts: [Copernicus Data Space](https://dataspace.copernicus.eu) (create a Sentinel Hub OAuth client) and the [Climate Data Store](https://cds.climate.copernicus.eu) (copy your personal access token, and accept the licence of `reanalysis-era5-land-timeseries` once on its Download tab).

```bash
uv sync
uv run geomcp setup      # asks for the credentials (hidden input), writes .env (mode 600), tests them live
uv run geomcp check      # re-test the credentials at any time
claude mcp add geomcp -- uv --directory "$PWD" run geomcp serve --stdio
```

Then ask, for example:

> Using geomcp: how have NDVI, air temperature and precipitation changed in gmina Kąty Wrocławskie over 2016-2025? Also give its terrain and current air quality, and show a recent satellite image.

Configs for the Claude app and other MCP clients are in [docs/clients](docs/clients/README.md).

## Speed and cost

Measured in October 2026; details in [ADR 0003](docs/adr/0003-hosted-engines-caching-and-cost.md).

| Question | Cold | Repeat |
|---|---|---|
| 10 years of Sentinel-2 indices for one area (10-180 km²) | 20-40 s, about 290 Sentinel Hub processing units | milliseconds, free |
| 10 years of 7 ERA5-Land weather variables | about 12 s | milliseconds |
| Summary of a 177 km² gmina (terrain, NDVI, weather, NO₂) | about 25 s, about 10 units | milliseconds |
| PNG snapshot | 1-3 s, under 1 unit | |

The free Sentinel Hub tier allows 10,000 units per month; `list_data` shows this server's usage and flags `quota_low` above 80%.
Everything fetched is cached under `GEOMCP_DATA_DIR` (default `~/.geomcp`): settled years forever, recent months for 6 hours.

## Deploy on a VPS

geomcp runs as one Docker container serving plain HTTP behind your HTTPS proxy (nginx), or behind the built-in Caddy.
Step-by-step guide, nginx config, access keys, usage stats and backups: [docs/deploy-vps.md](docs/deploy-vps.md).

```bash
cp .env.example .env && chmod 600 .env    # credentials, GEOMCP_PUBLIC_URL, GEOMCP_BIND
docker compose up -d --build
docker compose exec geomcp geomcp check
docker compose exec geomcp geomcp-admin keys create --name anna
docker compose exec geomcp geomcp-admin stats
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GEOMCP_CDSE_CLIENT_ID`, `GEOMCP_CDSE_CLIENT_SECRET` | none | Copernicus Data Space Sentinel Hub OAuth client |
| `GEOMCP_CDS_KEY` | none | Climate Data Store personal access token |
| `GEOMCP_DATA_DIR` | `~/.geomcp` (`/data` in Docker) | SQLite file and cache |
| `GEOMCP_SYNC_BUDGET_S` | `45` | Longer calls become background jobs |
| `GEOMCP_SENTINEL_HUB_MONTHLY_UNITS` | `10000` | Quota used for the `quota_low` warning |
| `GEOMCP_CONTACT` | none | Email or URL sent in the User-Agent to public services such as Nominatim; set it for a team server |
| `GEOMCP_PUBLIC_URL` | `http://host:port` | Public HTTPS address; base of signed download links |
| `GEOMCP_BIND` | `127.0.0.1:8000` | Host address and port the container listens on (compose) |
| `GEOMCP_GUS_BDL_KEY` | none | Free GUS BDL key: 5x the anonymous limit of 1,000 requests per 12 hours; recommended for a team |
| `GEOMCP_GUS_SMUP_KEY` | none | Optional free GUS SMUP key for higher rate limits |
| `GEOMCP_REGON_KEY` | none | GUS REGON (BIR) key: adds sole traders, non-VAT payers and PKD codes to `lookup_business` |
| `GEOMCP_CEIDG_TOKEN` | none | CEIDG token: enables `find_businesses` and sole-trader details in `lookup_business` |
| `GEOMCP_REGON_URL` | production | Override for the GUS test service (development only) |
| `GEOMCP_HOST`, `GEOMCP_PORT` | `127.0.0.1`, `8000` (`0.0.0.0` in Docker) | HTTP bind address |
| `GEOMCP_GOOGLE_CLIENT_ID`, `GEOMCP_GOOGLE_CLIENT_SECRET` | none | Google sign-in for clients that connect with just the address (Claude Desktop, claude.ai) |
| `GEOMCP_ALLOWED_EMAILS` | none | Who may sign in with Google: comma-separated emails or `@domain`; required with Google sign-in |
| `GEOMCP_OAUTH_REDIRECT_URIS` | Claude's callbacks and loopback ports | Where Google sign-in may send OAuth clients back; extend it for clients other than Claude |
| `GEOMCP_ALLOWED_HOSTS` | unset | JSON list of accepted Host headers; other names get HTTP 421 (optional hardening behind nginx); unset, a loopback bind accepts only localhost names |
| `GEOMCP_LOG_LEVEL` | `INFO` | JSON logs on stderr |
| `GEOMCP_ENABLE_SYNTHETIC` | `false` | Synthetic demo sources (tests and demos) |

A source without credentials shows as `unconfigured` in `list_data`; other sources keep working.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest -q                                        # offline: unit, MCP contract, transports, recorded HTTP
uv run pytest -q -m "live and not costly"               # real services, a few processing units
uv run --group crosscheck pytest -q -m costly           # performance and Earth Search cross-check, about 600 units
uv run pytest tests/recorded --record-mode=rewrite      # refresh cassettes (secrets are scrubbed)
uv run python scripts/make_tool_examples.py             # regenerate TOOL_EXAMPLES.md from real calls
docker build -t geomcp:dev .
```

Layout: `packages/core` (engine, no web or MCP dependencies), `packages/server` (MCP server, jobs, auth, CLI), `tests/`, `docs/adr/` (decisions), `scripts/`.
Adapters register through the `geomcp.adapters` entry-point group; `packages/core/src/geomcp_core/adapters/synthetic.py` is the minimal example.
