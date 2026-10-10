# ADR 0005: Operations for a small team (M3)

Status: accepted (M3), for a team of about four people.

## Decision

- No per-person limits or quotas: the people using geomcp are trusted colleagues.
  Provider protections stay, because they protect the free data accounts rather than restrict people: area caps per source, the monthly Sentinel Hub quota warning, and request pacing toward Nominatim, GUS and GIOŚ.
- Usage is visible instead of limited: every tool call is logged in SQLite (person, tool, outcome, duration, processing units), summarised by `geomcp-admin stats`, and exposed as Prometheus counters on `/metrics`, which needs an API key.
- geomcp serves plain HTTP; TLS ends at the team's own nginx (certificates managed outside geomcp).
  Caddy remains as an opt-in compose profile for deployments without a proxy.
- State is one Docker volume, backed up with `tar` (docs/deploy-vps.md).

## Consequences

- One heavy user can still spend the shared Sentinel Hub quota; `geomcp-admin stats` shows who, and `list_data` warns at 80%.
- Adding per-key limits later is a small change in the tool guard, if the team grows.
