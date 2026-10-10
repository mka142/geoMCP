# ADR 0007: Prices, zoning, traffic and orthophoto (M4 wave C)

Status: accepted, October 2026.

## Decision

- Real-estate prices come from the national price register (RCN) WFS, open since 13 February 2026.
  Only aggregates leave geomcp: yearly median, quartiles and counts for apartments (PLN/m², new and resale), houses (PLN) and undeveloped plots (PLN/m²).
  Only single-property free-market sales are priced: bundles (a developer's 59 parcels, apartments listed with their land shares) carry per-record prices that do not add up, and plots under 100 m² are land shares or garages.
  The server ignores `PROPERTYNAME`, so notary names and deed numbers arrive in every response; they are dropped at parse time and scrubbed from test cassettes.
  With the URN form of EPSG:2180 the service expects and returns northing first; Wrocław hides mistakes because its easting and northing are both about 362 km, so the axis order was verified in Kraków.
- Zoning comes from the national MPZP integration (KIMPZP) by GetFeatureInfo at sample points (a parcel's representative point, or a grid of up to 16 points on areas up to 5 km²).
  Every gmina publishes its own schema, so HTML tables are parsed generically and known fields are picked by name, with all fields kept as attributes.
  Some gminas, Wrocław among them, do not publish to the integration; the answer says so instead of claiming there is no plan.
  For a parcel, `get_zoning` also returns its cadastre facts from KIEG (registry area, register group, land-use class), as far as the powiat publishes them (Wrocław does; one Smolec parcel had empty classes); area-wide class shares are not sampled.
- Traffic comes from the GDDKiA General Traffic Survey 2020/21, which has no geometry: national roads crossing the area come from OpenStreetMap refs, joined by road number and voivodeship, and segments are flagged when their junction names mention a town within about 7 km (with GDDKiA's abbreviations such as "KĄTY WR.").
  It is marked experimental; GPR 2025 replaces it once published as data.
- The GUGiK orthophoto is a snapshot layer, drawn in CRS:84 with a cos(latitude) aspect correction so it is north-up; blank high-resolution renders fall back to the standard service.

## Skipped this wave

- PRG boundaries: areas already come from ULDK and OpenStreetMap.
- BDOT10k: a bulk download per powiat, not a query service; OpenStreetMap covers building counts.
- TERYT: needs a GUS account.

## Wave D (first part): companies

- `lookup_business` takes a KRS, NIP or REGON number.
  The VAT whitelist of the Ministry of Finance (no key) turns a NIP or REGON into a KRS number and adds the VAT status; the KRS open API (no key) gives the current extract.
  NIP and KRS numbers both have 10 digits: the NIP checksum and the leading zeros of KRS numbers tell them apart.
- KRS masks natural persons' names, so boards are reported by role; whitelist bank account numbers are not kept.
  A past merger or transformation is history, not a warning; arrears, curator, liquidation, bankruptcy and restructuring entries are warnings.
- Search by name waits for the GUS REGON key (BIR), and sole traders' details for the CEIDG token.
