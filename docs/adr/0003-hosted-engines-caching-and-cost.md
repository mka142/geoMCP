# ADR 0003: Hosted engines, per-chunk caching and processing-unit cost

Status: accepted (M1). Every number below was measured live in October 2026 over the golden AOI (10 km², Wrocław) or gmina Kąty Wrocławskie (177 km²).

## Satellite statistics: Sentinel Hub Statistical API on CDSE

- One request per cache chunk returns one value per acquisition (`P1D` intervals), with mean, p10, p90 and the cloud-free share.
  Settled years are one chunk; the current year is fetched by month.
  Any cadence (week, month, quarter, year) is then computed locally, so changing the cadence costs no quota.
- Statistics resolution targets ~15,000 pixels: 30 m for 10 km², 150 m for a 177 km² gmina.
  Cost is therefore roughly constant per area: about 280-290 PU for 10 years of Sentinel-2 (all six indices in one request), against a free quota of 10,000 PU per month.
  At 10 m the same pull would cost about 2,450 PU.
- Sentinel Hub downsamples with NEAREST by default, which biased 30 m NDVI by about -0.024 against 10 m in the live cross-check.
  We request BILINEAR down- and upsampling; Sentinel Hub then agrees with an independent Earth Search / odc-stac computation at 10 m within 0.000-0.005 on five clear dates.
- Ten yearly chunks run in parallel (10 slots): 10 years of Sentinel-2 in about 20-40 s cold.
- The Statistical API computes over the bounding-box grid and counts pixels outside the polygon as no-data, so the cloud-free share is divided by the polygon's fill of its box at read time (0.5 for gmina Kąty Wrocławskie); an answer with `status: PARTIAL` (days that timed out) is cached for hours, not forever.
- Sentinel-5P is slow per day (about 22 s per month), so it is always fetched in parallel monthly chunks.
- Copernicus DEM GLO-30 is forbidden for this account (403); the default `dem` collection is GLO-90, used through the Process API (the Statistical API returns no intervals for a timeless collection), with elevation and slope computed locally.
  GLO-90 is a surface model: buildings and canopy raise heights and steepen slopes, and results say so.

## Weather: CDS ERA5-Land point time series

- `reanalysis-era5-land-timeseries` returns hourly values for the grid cell nearest to the area centroid; one job per calendar year, 4 in parallel: 10 years of 7 variables in about 12 s cold.
- Unlike the gridded ERA5-Land product (guide §10 gotcha 5), precipitation and radiation arrive as hourly amounts, not accumulations: daily totals are plain sums (2024 at Wrocław: 821 mm).
- One CSV can hold several variables (`d2m` and `t2m`, `u10` and `v10`); the parser reads every value column.
- The latest ~90 days are ERA5T and reported as `preliminary`.
- The guide §7.7 ARCO store is not used: its chunks are 1 hour x the whole globe (about 1 TB for a 10-year point series).
  The Earthmover Icechunk `single/temporal` fallback is deferred.

## Places

- Nominatim (1 request/s, polygons, TERYT codes in extratags) for names and gminas; GUGiK ULDK for parcels; coordinates locally.
- GUGiK PRG WFS serves only GML (no GeoJSON) and needs EPSG:2180 axis handling; official PRG geometry is a later refinement.

## Consequences

- A cold 10-year question for one area costs about 290 PU and 20-60 s; repeats cost nothing and take milliseconds.
- `list_data` reports this server's monthly PU use and flags `quota_low` above 80%.
- Answers report the statistics resolution and method, so users can judge fitness for purpose.
