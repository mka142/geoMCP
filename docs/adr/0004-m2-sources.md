# ADR 0004: M2 sources (download links, OSM, land cover, GUS, GIOŚ, GUGiK NMT)

Status: accepted (M2). Behaviour below was measured live in October 2026.

## Download links

- Full tables (every row, not the sample in the tool result) are written once as CSV and Parquet, named by the SHA-256 of their content.
- stdio mode returns local paths; HTTP mode returns `/files/<name>?exp=&sig=` links signed with a per-installation HMAC secret and valid for 24 hours, so a browser or spreadsheet can fetch them without the API key.

## OpenStreetMap (Overpass)

- One query per area returns counts (buildings, schools, healthcare, shops, transit stops) and road geometry, clipped to the polygon and measured geodesically; cached 7 days; areas up to 250 km².
- The main Overpass instance was overloaded during testing (504s); a mirror (`overpass.kumi.systems`) is tried next.

## ESA WorldCover

- Read with rasterio from the cloud-optimised GeoTIFFs on AWS Open Data: only the area's window is fetched (1-2 s), so no raster store is needed.

## GUS Local Data Bank

- The gmina is the area's own TERYT code, or the gmina containing the area's centroid via GUGiK ULDK `GetCommuneByXY`, which also gives its name for a single BDL unit search.
- Listing all ~2,500 gmina units costs 25 requests and hit the anonymous limit (HTTP 429); the name search costs one, with a client-side limit of about 3 requests/s and an optional free API key.
- Variables were verified by id and real values: 72305 population, 60559 density, 453193 net migration, 450551 natural increase, 747060 dwellings completed, 1548700 new businesses, 76037 budget income (divided by population).
  Registered unemployment (60270) exists only for powiats and is left out.

## GIOŚ stations

- The public API keeps only about 100 hours, so station variables are current snapshots (last 24 hours at the nearest station measuring the pollutant, within 25 km), reported through `get_summary`.
- Timestamps are Polish local time and are converted to UTC (guide §10 gotcha 17); requests are spaced 2 s apart and lists are cached (gotcha 18).
- Pages larger than 100 rows are rejected with HTTP 400.

## GUGiK NMT

- WCS 1.0.0 in EPSG:2180 with the bounding box in easting, northing order, as the coverage description publishes it (gotcha 7).
- Extents larger than about 4 km come back mostly zeros (82% for a 13 km gmina) while the data exists, so requests are tiled at 4 km and 0.0 is treated as no data.
- The service drops connections now and then; retries cover it. Heights are PL-KRON86-NH and are never mixed with EGM2008 (gotcha 8).

## Deferred

- CAMS (needs a NetCDF stack; Sentinel-5P and GIOŚ already cover air quality) and IMGW history (zipped CP1250 archives; ERA5-Land covers weather, and IMGW is restricted).
- Google Earth Engine, until there is a Google Cloud project and a decision on its commercial terms.
