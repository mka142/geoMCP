# ADR 0008: Noise, drought, air-quality history, traffic 2025, Europe-wide facts and registers

Status: accepted, October 2026.

## Decision

- Noise (GIOŚ strategic maps, round 3 of 2017) is rendered, not downloaded: the WFS returns whole city-wide band polygons (18 MB for one small box), so the WMS draws each layer with our own SLD that paints the band's lower bound into the red channel, with anti-aliasing off, and pixels are counted per source (road, rail, aircraft, industry), taking the louder of the major-infrastructure and agglomeration maps.
  The newer 2022 maps exist only in an API without a spatial filter (1.8 GB per voivodeship), so they are not used.
- Agricultural drought comes from IUNG's per-gmina pages (`/wykazy/{year},{TERYT}/`), parsed from HTML, with the soil category under the area from IUNG's GeoServer to put the relevant table first.
- Air-quality history uses the GIOŚ statistics service (verified annual means since 2000), per voivodeship, cached for 30 days; the API rate-limits hard.
- Traffic moved to GPR 2025 (published 30 September 2026): each measured section has a measurement point, so per road crossing the area the sections measured inside it, or else the nearest within 15 km, are reported, with the 2020/21 count of the same section.
  Vehicle columns p1-p8 are ordered motorcycles, cars, minibuses, vans, trucks, trucks with trailers, buses, tractors, as the values show (minibuses are the new 2025 category).
  e-TOLL monthly totals (GUS TranStat) add a 12-month heavy-vehicle trend for tolled roads; they are whole-road totals, not local counts.
- Europe-wide facts, all keyless: GHSL population (100 m, read by window from zipped tiles over HTTP), PVGIS solar potential, CORINE land cover 1990-2018 (vector query, geometry generalised to 25 m, clipped locally), and Open-Meteo climate projections.
  Open-Meteo's free API is non-commercial, so `climate_projection` is flagged `commercial_safe=false`; the CDS CMIP6 route works with our key but takes a minute per variable and is raw 100-200 km model output.
- Buildings by function come from BDOT10k per-powiat downloads (about 30 MB zipped), stream-parsed once into a cached column list.
  Its GML lists EPSG:2180 easting first; Wrocław's coordinates cannot tell the orders apart, so the check counts buildings near Kąty Wrocławskie's centre.
- REGON (BIR) is wired and tested against GUS's test service; with a key it adds sole traders, non-VAT payers and PKD codes to `lookup_business`.
- CEIDG (token from biznes.gov.pl) powers `find_businesses` (sole traders by name, gmina, town and PKD, with the total count from one call) and sole-trader details in `lookup_business`.
  CEIDG is slow and its gateway answers a long query with an HTML page and status 200 (a PKD search took 90 s for 20 rows, 30 s for one), so pages hold 5 rows, fall back to the count alone, and non-JSON answers count as a busy source.
  Calls are paced 3.7 s apart: going over 50 per 3 minutes locks the token out for 3 minutes.

## Not done, with reasons

- PRG boundaries: OpenStreetMap's gmina boundaries already match PRG (Kąty Wrocławskie 176.7 km² in both), so nothing would change.
- TERYT: the SOAP service never answers; the keyless eTERYT files would add only locality and street lists, which Nominatim already resolves.
- EGMS outside Poland: needs an EU Login token; the public viewer tiles use an undocumented binary format.
- Company search by name: no free register offers it (REGON and KRS look up numbers only).
