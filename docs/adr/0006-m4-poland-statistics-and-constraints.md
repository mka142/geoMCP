# ADR 0006: Polish statistics, hazards and constraints (M4)

Status: accepted (M4 waves A and B), October 2026.

## Decision

- Statistics are generic, not hand-picked: `find_statistics` searches GUS BDL (variables and subjects, Polish words only) and SMUP (Polish and English names, matched locally by word stems), and `get_statistics` returns the gmina with its powiat, voivodeship and Poland for comparison.
  Indicator ids carry their source (`bdl:72305`, `smup:10`).
- Hazards and constraints are one tool, `get_constraints`, returning shares of the area (0-1) with names, measured in EPSG:2180.
- Flood zones come from the ISOK WMS (Wody Polskie): the vector downloads are national shapefiles of about 330 MB each and the REST services need a login, while the WMS is open.
  The WMS layers draw only at 5 m per pixel or finer (measured: nothing at 10 m), so the area is rendered at 5 m in tiles of up to 1,500 pixels and coloured pixels inside the polygon are counted.
  The service paints an opaque white background even with `TRANSPARENT=TRUE`, so pixels are counted by colour, not alpha.
  Only the water-depth and hazard-zone layers are drawn (October 2026 review): each service also publishes rivers, all surface waters, embankments, labels and a sheet index, and counting them made every lake a flood zone and added grid lines everywhere.
  Areas are capped at 250 km² (a 177 km² gmina takes about 60 s cold, 4 scenarios).
  A scenario whose service fails is reported as a named gap; the other scenarios still answer, and no share is guessed for the missing one.
- Nature protection comes from the GDOŚ GeoServer WFS (GeoJSON), geological hazards from PIG-PIB ArcGIS REST (GeoJSON), and vertical ground motion from the PIG-PIB copy of EGMS 2016-2020 through `computeStatisticsHistograms`, sent as POST because polygons exceed URL limits.
  The SOPO landslide layers refuse the query operation (HTTP 200 with an error body, October 2026) but answer `identify` with geometry, so landslides are read through `identify`.
  It returns at most 1,000 features without flagging truncation, so a full answer splits the tile into quarters down to about 1 km, and a full answer at that size is a gap.
  A provider error or a truncated feature list is never read as "none here".
- Constraint results are cached for 30 days, or for a day when a source group could not answer (the gaps are cached with them); current IMGW warnings are never cached with them.
- Climate extremes (hot days, tropical nights, frost days, heavy-rain and dry days) are derived at read time from the cached ERA5-Land daily values, so they cost no extra downloads.

## Deferred, with reasons

- STRATEG: no published API specification was found.
- GUS 1 km population grid: the geostatistics portal answers 401; the EU wave adds the Eurostat census grid instead.
- IUNG agricultural drought per gmina: only HTML tables and map tiles, no data service.
- GIOŚ noise maps: the API has no published specification and returns no records for any combination tried.
- KLIMADA projections: portal only; the EU wave uses EURO-CORDEX through the CDS instead.

## Consequences

- Anonymous GUS BDL allows 1,000 requests per 12 hours, which statistics searches can exhaust in a busy day; a long block now fails fast with the reset time instead of retrying, and the free key is recommended.
- Flood shares from rendered maps are accurate to about one 5 m pixel along zone edges, negligible for areas of hectares and more.
