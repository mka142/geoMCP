# ADR 0001: MCP-first server on a single node

Status: accepted (M0).

## Context

The users are executives and researchers asking geodata questions through LLM clients.
Benchmarks over a 10 km² Wrocław AOI showed that hosted engines (Sentinel Hub Statistical, CDS time series) answer in seconds to minutes, while our own pixel downloads take 30 min to hours for 10 years.

## Decision

- A core library (`geomcp-core`) holds every capability; the MCP server (`geomcp-server`) is a thin, hand-written tool layer over it.
- Tools return compact structured results plus a text summary; long series are summarised (seasonal Theil-Sen trend, seasonal Mann-Kendall p-value, extremes, gaps) and sampled to `max_rows` whole periods.
- Two runtimes: stdio (local, no auth, no database needed) and Streamable HTTP in one container behind Caddy.
- State is one SQLite file through SQLAlchemy and Alembic, with portable SQL, so Postgres is a later URL change.
- Area ids are self-describing (`a_pt_<lat>_<lon>_<sq|ci><metres>`), so no area state is shared between processes.

## Consequences

- No queue, Redis or Postgres to operate; one volume to back up.
- Long jobs (M1+) run in-process with a job table; scaling out later means Postgres plus worker processes.
