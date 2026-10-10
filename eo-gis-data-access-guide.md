# Programmatic Access to GIS & Earth Observation Data — Technical Reference

**For:** a multi-modal data-fusion MVP that returns batches `(AOI × time window)`, e.g. one week, across as many modalities as possible
**Coverage:** Poland (national sources) and global/European sources
**Status date:** 2026-10-06. Endpoints, quotas and licences were checked against official pages on that date.
**Audience:** developers and coding agents (Claude Code). Every source lists its doc URL so an agent can open it before writing code.

> **Verification legend**
> - **[V]** verified against an official page or a live response on 2026-10-06.
> - **[U]** unverified, or taken from a client library or memory. Test it before you rely on it.
> - Untagged items are stable, well-known facts.

## Contents

0. How to use this document · 1. Scope and design decisions · 2. Reference architecture · 3. Access platforms comparison · 4. Modality catalog (4.1 optical · 4.2 SAR · 4.3 thermal · 4.4 topography · 4.5 meteo/precipitation · 4.6 hydrology/soil moisture · 4.7 air quality · 4.8 noise · 4.9 population · 4.10 land cover · 4.11 embeddings · 4.13 Poland national services) · 5. Derived indicators · 6. Temporal batching · 7. Code recipes · 8. Output specification · 9. Licensing and cost · 10. Gotchas · 11. Open decisions · 12. Link index

---

## 0. How to use this document (for Claude Code)

1. Pick the modality you need in **§4 Modality catalog**. Each entry gives native resolution, temporal coverage, access path(s), licence and doc links.
2. Pick the access backend in **§3 Platform comparison**. The default is **open STAC + `odc-stac` → xarray**.
3. Copy the matching recipe from **§7 Code recipes**. Each recipe returns an `xarray.Dataset` with dims `(time, y, x)` or `(time, station)`.
4. Normalise the result with **§6 Temporal batching** and **§8 Output spec** (Zarr/NetCDF DataTree + optional COG export).
5. Before you ship anything commercial, check **§9 Licensing**.
6. Before guessing an endpoint, open the doc URL in **§12 Link index**. Several services changed in 2025–2026: the CDSE OpenSearch catalogue was switched off, the GIOŚ API moved to v1, xee had a breaking 0.1.x release, and the Copernicus licence became CC-BY.

---

## 1. Scope and design decisions

| Decision | Choice | Consequence |
|---|---|---|
| Data philosophy | **Reduced form.** Pull physical primitives (surface reflectance bands, SAR backscatter, DEM, meteorological variables, concentrations, counts) and compute indicators yourself. | One Sentinel-2 load can yield NDVI, NDWI, MNDWI, NDMI, NBR, NDBI and so on. Avoid buying or downloading a separate "product per indicator". |
| Spatial model | **Multi-resolution.** Each modality stays at its native grid; a common grid is produced only on request. | The output is a *DataTree*: one group per native resolution, plus an optional `/common` group. |
| History | Depends on the modality: Landsat from 1982, Sentinel from 2014/2015, ERA5 from 1940. | Each adapter declares `temporal_coverage`; the batch returns what exists and flags gaps. |
| Licence policy | **Open data only**, commercial use allowed. Paid options are listed only where a small fee removes real pain (§9.2). | Avoid NC/SA-licensed sources in the product path: FABDEM, MSWEP, WAQI, and TESSERA (SA). |
| Output | `xarray` → **Zarr v3 / NetCDF4** (primary) and **Cloud-Optimized GeoTIFF** (for GEE ingestion and GIS tools). | See §8. |
| Compute | Laptop + VPS for the MVP, with cloud compute next to the archives for heavy pulls. | Prefer streaming COG reads over HTTP. Run heavy jobs in the same region as the data (AWS us-west-2 / eu-central-1, Azure West Europe, CloudFerro WAW). |
| Platforms | All backends are described; the default is open STAC. | Backend adapters are pluggable (§2). |

---

## 2. Reference architecture of the fusion service

```
Request ──► Planner ──► Source adapters (parallel) ──► Harmoniser ──► Writer
 {aoi, t0, t1,          │  stac_s2 / stac_landsat /        │ cloud mask,   │ Zarr/NetCDF DataTree
  cadence, modalities,  │  s1_rtc / dem / era5 / cams /    │ scale/offset, │ COG per variable×period
  grid_policy}          │  imgw / gios / gus / osm / ...   │ temporal agg, │ STAC item (provenance)
                        └── cache (Zarr/Parquet, keyed by  │ reprojection  │
                            source+aoi_hash+period)        └───────────────┘
```

### 2.1 Request model

```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class BatchRequest:
    aoi: dict                         # GeoJSON geometry, EPSG:4326
    start: str                        # ISO date, inclusive; Monday when cadence="7D"
    end: str                          # ISO date, exclusive
    cadence: str = "7D"               # pandas offset alias used for temporal aggregation
    modalities: list[str] = field(default_factory=lambda: ["s2", "s1", "dem", "era5land"])
    grid_policy: Literal["native", "common"] = "native"
    common_crs: str | None = None     # e.g. "EPSG:2180" (PL), "EPSG:3035" (EU), UTM zone (local)
    common_res: float | None = None   # metres; only for grid_policy="common"
    max_cloud: int = 60               # STAC pre-filter, percent
```

### 2.2 Adapter contract

Every adapter implements the same three methods, so the planner can fan out and the harmoniser can align results:

```python
class SourceAdapter(Protocol):
    name: str                       # "s2_l2a", "era5_land", "gios_aq"...
    native_res: str                 # "10m", "0.1deg", "station"
    temporal_coverage: tuple[str, str | None]
    def search(self, req: BatchRequest) -> list: ...          # STAC items / file list / API pages
    def load(self, req: BatchRequest, hits) -> "xr.Dataset": ...  # raw, CF-named, lazy (dask)
    def aggregate(self, ds, cadence) -> "xr.Dataset": ...      # per-variable reducer (see §6)
```

### 2.3 CRS conventions

| Use | CRS | Why |
|---|---|---|
| Native Sentinel-2/Landsat | Local **UTM** (PL: EPSG:32633 west of 18°E, EPSG:32634 east of 18°E) | No resampling of source pixels |
| Poland national common grid | **EPSG:2180** (PUWG 1992) | Native CRS of GUGiK, BDOT10k, PRG and EGiB data |
| Europe common grid | **EPSG:3035** (LAEA) | Equal-area; native CRS of Eurostat grids and CLMS HRL |
| Coarse reanalysis/atmosphere | EPSG:4326 regular lat/lon | Native for ERA5 and CAMS; resample only on export |

An AOI that crosses 18°E spans two UTM zones. Pick one zone (by centroid) and let `odc-stac` reproject.

### 2.4 Caching and provenance

- Cache key: `sha1(source, collection_version, aoi_wkb, period, grid)`. Store as Zarr (rasters) or GeoParquet (vectors/stations).
- Every output variable gets attributes: `source`, `collection`, `item_ids` (or file URLs), `processing_baseline`, `licence`, `attribution`, `retrieved_at`, `aggregation`, `valid_fraction`.

---

## 3. Access platforms — comparison and selection

### 3.1 Summary matrix

| Platform | What it is | Auth | Cost (free tier) | Where compute runs | Strength | Weakness |
|---|---|---|---|---|---|---|
| **Element 84 Earth Search** (STAC) | Catalogue of AWS Open Data: S2, Landsat, S1, Copernicus DEM | None | Free; **S1 GRD and Landsat assets are requester-pays** | Yours | Fastest path to S2 L2A COGs; `sentinel-2-c1-l2a` already harmonised | S1 and Landsat cost money in AWS; no compute |
| **Microsoft Planetary Computer** (STAC) | Large curated STAC catalogue on Azure (West Europe) | None; SAS signing via `planetary-computer` | Free (rate-limited; optional key) | Yours | **S1 RTC** (terrain-corrected), Landsat C2 L2, HLS v2, S5P, ESA WorldCover, IO LULC, MS buildings | Hub retired in 2024; some collections are stale (e.g. `era5-pds`) |
| **Copernicus Data Space Ecosystem (CDSE)** | ESA's official Copernicus archive with STAC, OData, S3, Sentinel Hub, openEO and JupyterLab | Free account (OIDC); S3 keys | Sentinel Hub 10k requests and 10k PU per month; openEO 10k credits/month; S3 limited to 4 connections × 20 MB/s, 12 TB/30 days | Yours (S3/STAC) or **server-side** (Sentinel Hub, openEO) | Complete Copernicus archive (S1/S2/S3/S5P, CLMS moving in), EU-sovereign, hosted in Poland (CloudFerro WAW) | S3 parallelism cap; asset auth adds friction |
| **Google Earth Engine** | Planetary-scale server-side compute with a huge catalogue | Google Cloud project | Noncommercial tiers from 2026-04-27: Community 150 EECU-h/month, Contributor 1,000. **Commercial use requires a paid plan** ($500/month Basic, $2,000 Pro) | Server-side | Fastest prototyping; Dynamic World; **Satellite Embedding (AlphaEarth)**; community catalogue | Licence and cost for commercial use; data egress limits; vendor lock-in |
| **NASA Earthdata** (CMR-STAC + `earthaccess`) | NASA DAACs: HLS, IMERG, SMAP, MODIS/VIIRS, GRACE, OPERA | Free Earthdata Login | Free | Yours (direct S3 only from us-west-2) | HLS v2 (30 m, 2–3 day revisit), IMERG, SMAP, OPERA DSWx | Many products are HDF5/NetCDF granules, not COGs |
| **ASF** (`asf_search`, HyP3) | SAR archive (S1, NISAR) plus on-demand RTC and InSAR processing | Earthdata Login | HyP3: 8,000 free credits/month (RTC 5–60 credits per job) | ASF servers | On-demand S1 RTC/InSAR; **NISAR L-band** (public since 2026-07-20) | Job queue latency |
| **CDS / ADS / EWDS** (ECMWF) | Climate, atmosphere and emergency data stores | Free account + token; accept each dataset's licence once | Free (queue-based) | ECMWF (request queue) | ERA5, ERA5-Land, CAMS, GloFAS/EFAS | Asynchronous queue; large requests wait |
| **ARCO / Icechunk Zarr mirrors** | Analysis-ready ERA5 in cloud Zarr (Google, Earthmover) | None | Free | Yours | Instant slicing of ERA5 in xarray, no queue | Lag of 1 week to 3 months behind the CDS |

### 3.2 Recommended combination for the MVP

1. **Default raster path:** `pystac-client` + `odc-stac` against **Earth Search** (S2 C1 L2A) and **Planetary Computer** (S1 RTC, Landsat C2 L2, HLS, S5P, WorldCover). Everything is lazy dask reads of COGs.
2. **EU/Copernicus completeness and fallback:** **CDSE STAC/S3**, for S3 OLCI/SLSTR, S5P, CLMS soil moisture and full S1. Use **openEO or the Sentinel Hub Statistical API** when you only need *aggregates* (weekly mean NDVI per polygon). These return kilobytes, not gigabytes.
3. **Reanalysis:** ARCO-ERA5 or Earthmover Icechunk for slicing; **CDS API** for ERA5-Land (0.1°) and for the most recent days.
4. **GEE as an optional sidecar:** use it for datasets that only exist there (Dynamic World) or are easiest there (Satellite Embedding), and as a consumer of your COGs (§7.9). Keep it out of the critical path unless you buy a commercial plan.
5. **Polish national data:** direct OGC services (WCS/WFS/WMS) and REST APIs (§4.13, §7.12–7.15).

### 3.3 Decision rules

- **Pixel data you will fuse at native resolution** → STAC + odc-stac.
- **Only a time series of aggregates per polygon** → Sentinel Hub Statistical API, openEO `aggregate_spatial`, or GEE `reduceRegions`. This cuts transfer by orders of magnitude.
- **Hourly meteorology over a long period** → ARCO Zarr (point or area slices), not the CDS queue.
- **Station observations** → national APIs (IMGW, GIOŚ) and the EEA download service. Keep them as `(time, station)` and *rasterise only on request*.

---

## 4. Modality catalog

Every entry follows the same layout: **native resolution · temporal coverage and cadence · primitives to pull · access paths (collection IDs) · licence · docs**.

### 4.1 Optical multispectral (vegetation, water, built-up, moisture, burn)

#### Sentinel-2 MSI Level-2A (surface reflectance) — primary optical source

| Property | Value |
|---|---|
| Bands | B01 coastal 60 m · **B02 blue, B03 green, B04 red, B08 NIR 10 m** · B05–B07 red-edge, B8A narrow NIR, **B11 SWIR1, B12 SWIR2 20 m** · B09 60 m · **SCL** scene classification 20 m |
| Revisit | 5 days with 2 satellites. In 2025–2026 S2B + S2C are nominal and S2A runs an extended campaign until end of 2026 [V], so effective revisit over PL is about 2–3 days. |
| History | L1C from 2015-06. With the Collection-1 reprocessing (baselines 05.00/05.10, completed 2025 [V]), a consistent L2A archive exists from 2015 on CDSE. |
| Scale/offset | **Since processing baseline 04.00 (2022-01-25): reflectance = (DN − 1000) / 10000** (`BOA_ADD_OFFSET = −1000`). Earth Search `sentinel-2-c1-l2a` declares scale 0.0001 and offset −0.1 for every item [V]. CDSE, Planetary Computer and GEE `S2_SR` deliver raw DN, so read `s2:processing_baseline` or the item's `raster:bands` scale/offset. GEE `COPERNICUS/S2_SR_HARMONIZED` is already shifted. |
| Cloud mask | SCL classes: keep 4 vegetation, 5 bare soil, 6 water, 7 unclassified, 11 snow (optional); drop 0 no-data, 1 saturated, 2 dark/topographic shadow, 3 cloud shadow, 8/9 cloud medium/high, 10 thin cirrus. For better masks use **s2cloudless** (GEE `COPERNICUS/S2_CLOUD_PROBABILITY`) or **Cloud Score+** (GEE `GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED`). |
| Licence | Copernicus free, full and open data policy. Commercial use allowed with attribution "Contains modified Copernicus Sentinel data [year]". |

| Backend | Collection ID | Asset naming | Notes |
|---|---|---|---|
| Earth Search v1 | `sentinel-2-c1-l2a` **(use this)**; `sentinel-2-l2a` is legacy | `blue, green, red, nir, nir08, rededge1-3, swir16, swir22, scl, coastal, nir09` | No auth, COGs on AWS eu-central-1. Possible archive gaps during reprocessing [U]. |
| Planetary Computer | `sentinel-2-l2a` | `B02 … B12, SCL` | Sign with `planetary_computer.sign_inplace`. |
| CDSE STAC | `sentinel-2-l2a` (STAC 1.1, `https://stac.dataspace.copernicus.eu/v1/`) [V] | Inspect `item.assets.keys()` (resolution-suffixed keys) | Assets need S3 keys or an OIDC token. |
| GEE | `COPERNICUS/S2_SR_HARMONIZED` | `B2 … B12, SCL, MSK_CLDPRB` | Server-side. |

Docs: [SentiWiki S2](https://sentiwiki.copernicus.eu/web/s2-processing) · [CDSE S2 baselines](https://documentation.dataspace.copernicus.eu/Data/Others/Sentinel2_L2A_baseline.html) · [Earth Search](https://github.com/Element84/earth-search) · [PC S2](https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a)

#### Landsat Collection 2 Level-2 (SR + surface temperature) — long history

| Property | Value |
|---|---|
| Sensors | Landsat 4/5 TM (1982–2013), Landsat 7 ETM+ (1999–2024; SLC-off stripes after 2003-05; decommissioned 2025-06 [V]), Landsat 8 OLI/TIRS (2013–), Landsat 9 (2021–). Landsat Next has been renamed **Landsat 10**, expected about 2031 [V]. |
| Resolution | 30 m reflective; thermal band ST_B10/ST_B6 resampled to 30 m (native 100/120 m) |
| Revisit | 16 days per satellite; 8 days with L8 + L9 |
| Scale/offset | **SR = DN × 0.0000275 − 0.2**; **ST (K) = DN × 0.00341802 + 149.0** |
| Cloud mask | `QA_PIXEL` bits: 0 fill, 1 dilated cloud, 2 cirrus (L8/9), 3 cloud, 4 cloud shadow, 5 snow, 7 water |
| Harmonisation | TM/ETM+ and OLI differ slightly. For long trends apply Roy et al. 2016 cross-sensor coefficients, or use HLS (below). |
| Licence | US Government public domain; attribution requested |

| Backend | Collection ID | Notes |
|---|---|---|
| Planetary Computer | `landsat-c2-l2` | Assets `blue, green, red, nir08, swir16, swir22, lwir11, qa_pixel`. Free and signed; **easiest option**. |
| Earth Search | `landsat-c2-l2` | Requester pays (AWS us-west-2) |
| USGS LandsatLook STAC | `landsat-c2l2-sr`, `landsat-c2l2-st` at `https://landsatlook.usgs.gov/stac-server` [V] | Assets in requester-pays `s3://usgs-landsat` |
| USGS M2M API | `https://m2m.cr.usgs.gov/` | Bulk ordering; ERS account plus access request; token login |
| GEE | `LANDSAT/LC08/C02/T1_L2`, `LANDSAT/LC09/C02/T1_L2`, `LANDSAT/LE07/…`, `LANDSAT/LT05/…` | |

Docs: [Landsat C2 L2 science products](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products) · [AWS registry](https://registry.opendata.aws/usgs-landsat/)

#### Harmonized Landsat Sentinel-2 (HLS v2.0) — analysis-ready 30 m fusion

- `HLSL30` (Landsat 8/9) and `HLSS30` (Sentinel-2A/B/C, S2C included since 2025-01 [V]). Common 30 m MGRS grid, BRDF-normalised and bandpass-adjusted. Together they give a 2–3 day revisit.
- New since 2025-02: `HLSL30_VI` / `HLSS30_VI`, nine precomputed vegetation indices. The historical backfill is planned for 2026 [V].
- Access: CMR-STAC `https://cmr.earthdata.nasa.gov/stac/LPCLOUD` (collections `HLSL30_2.0`, `HLSS30_2.0`) with `earthaccess` (free Earthdata Login). Planetary Computer has `hls2-l30` / `hls2-s30` (2020–) [V].
- Licence: NASA open data, no restrictions.
- **When to use it:** dense 30 m time series without writing your own L8/S2 harmonisation.
- Docs: [HLS](https://hls.gsfc.nasa.gov/) · [LP DAAC HLSS30](https://lpdaac.usgs.gov/products/hlss30v002/)

#### Sentinel-3 OLCI / SLSTR — daily, coarse

- OLCI: 300 m, 21 bands, daily. Used for water colour, chlorophyll and OTCI vegetation index. SLSTR: 500 m–1 km including thermal (LST, below).
- Access: CDSE (STAC/OData/S3, collections `sentinel-3-olci-*`, `sentinel-3-sl-2-lst-*`, IDs [U]; inspect the STAC collection list). GEE: `COPERNICUS/S3/OLCI`.

### 4.2 SAR (all-weather: soil moisture proxy, flooding, structure, change)

| Source | Resolution | History / cadence | Primitives | Access | Licence |
|---|---|---|---|---|---|
| **Sentinel-1 GRD IW** | 10 m pixel (≈20 m effective), C-band, VV+VH over land | 2014-10 onward. 12-day repeat 2022–2025 (S1B lost 2021-12). **S1C (2025) + S1D (user data since 2026-04-17) give a 6-day repeat**; S1A phased out July 2026 [V] | σ⁰ VV, VH (linear → dB) | CDSE STAC `sentinel-1-grd`; Earth Search `sentinel-1-grd` (requester pays); GEE `COPERNICUS/S1_GRD` (already σ⁰ dB, border-noise removed) | Copernicus open |
| **Sentinel-1 RTC** (radiometric terrain-corrected γ⁰) | 10 m | 2014 onward | γ⁰ VV, VH, **linear power** | PC `sentinel-1-rtc` (CC-BY-4.0, SAS token) [V]; ASF HyP3 on demand (RTC 5–60 credits; 8,000 free credits/month) [V]; OPERA `RTC-S1` at ASF | CC-BY / open |
| **NISAR** | L-band, 12-day repeat | Public release began 2026-07-20 for acquisitions since 2026-06-17; the full record is expected by end of 2026 [V] | GCOV (backscatter covariance), GUNW (interferograms) | ASF Vertex / `asf_search` / Earthdata Search | NASA open |
| **EGMS** (ground motion) | PS/DS points (L2a/L2b), 100 m grid (L3 ortho: vertical + E-W) | Current release covers **2020-01 to 2024-12**; annual updates; only the latest two releases online [V] | Displacement time series (mm) | [EGMS Explorer](https://egms.land.copernicus.eu) (free account; tokenised zip downloads); **no official REST/STAC API** [V] | Copernicus open |

Note: OPERA DISP-S1 / CSLC-S1 cover **North America only**. In Poland, ground motion has to come from EGMS or your own InSAR via HyP3.

Docs: [SentiWiki S1](https://sentiwiki.copernicus.eu/web/s1-mission) · [PC S1 RTC](https://planetarycomputer.microsoft.com/dataset/sentinel-1-rtc) · [asf_search](https://docs.asf.alaska.edu/asf_search/basics/) · [HyP3 credits](https://hyp3-docs.asf.alaska.edu/using/credits/) · [NISAR docs](https://nisar-docs.asf.alaska.edu/) · [EGMS product](https://land.copernicus.eu/en/products/european-ground-motion-service)

### 4.3 Thermal / land surface temperature

| Source | Res. | History | Access | Notes |
|---|---|---|---|---|
| Landsat C2 L2 ST | 30 m (100 m native) | 1982– | PC `landsat-c2-l2` asset `lwir11` | Scale per §4.1 |
| Sentinel-3 SLSTR `SL_2_LST` | 1 km, daily (day + night) | 2016– | CDSE | Copernicus open |
| MODIS MOD11/MYD11 v061 | 1 km, daily | 2000– | `earthaccess`, GEE `MODIS/061/MOD11A1` | **Terra/Aqua end of life late 2026 to early 2027. Plan to move to VIIRS** (`VNP21`) [V] |
| ECOSTRESS | 70 m, irregular overpass times | 2018– | `earthaccess` (LP DAAC) | Diurnal sampling; gaps |

### 4.4 Topography (static, but essential for every fusion)

| Source | Res. | Epoch | Access | Licence |
|---|---|---|---|---|
| **Copernicus DEM GLO-30** (DSM) | 30 m | 2011–2015 acquisition; 2021 release on AWS, **2024_1 release on CDSE/GEE** [V] | AWS `s3://copernicus-dem-30m` (COG, no auth); PC / Earth Search `cop-dem-glo-30`; GEE `COPERNICUS/DEM/GLO30` | Free with attribution, commercial OK |
| Copernicus DEM GLO-90 | 90 m | same | `s3://copernicus-dem-90m` | same |
| **GUGiK NMT (DTM)** | **1 m** (ALS), 5 m | Rolling ALS campaigns 2010–2025 | WCS (§4.13, §7.12), sheet-index WFS with download URLs | **Free for any use including commercial** (since 2020-07-31) [V] |
| GUGiK NMPT (DSM) | 0.5 m (cities) / 1 m | same | WCS `…/PZGIK/NMPT/GRID1/WCS/DigitalSurfaceModel` [V] | same |
| GUGiK LiDAR point clouds | 4–20 pts/m², LAS/LAZ | KRON86 2010–2019; EVRF2007 2018–2025 [V] | WFS sheet indexes `DanePomiaroweLidar{KRON86,EVRF2007}/WFS/Skorowidze` | same |
| FABDEM | 30 m (bare-earth) | — | — | **CC BY-NC-SA, so not for a commercial product** |

**Derive, don't download:** slope, aspect, curvature, hillshade, TPI/TRI, flow direction/accumulation, TWI (topographic wetness index) and HAND. Compute them from the DEM with `xarray-spatial`, `richdem`, `pysheds` or `whitebox`.

**Height datum:** Polish data is moving from PL-KRON86-NH to **PL-EVRF2007-NH**. Never mix the two in one mosaic. Copernicus DEM uses EGM2008.

### 4.5 Meteorology and precipitation

| Source | Res. | History · latency | Primitives (variable names) | Access | Licence |
|---|---|---|---|---|---|
| **ERA5** single levels | 0.25°, hourly | **1940–** · about 5 days (ERA5T, revisable) | `2m_temperature`, `2m_dewpoint_temperature`, `total_precipitation`, `10m_u/v_component_of_wind`, `surface_solar_radiation_downwards`, `surface_pressure`, `total_cloud_cover`, `volumetric_soil_water_layer_1-4`, `potential_evaporation`, `snow_depth`, … | CDS `reanalysis-era5-single-levels`; **ARCO Zarr** `gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3` (anonymous) [V]; **Earthmover Icechunk** `s3://earthmover-icechunk-era5/icechunkV2` (anonymous, 1940–2025, quarterly updates) [V]; NCAR `s3://nsf-ncar-era5` | **CC-BY 4.0** (Copernicus licence replaced on 2025-07-02 [V]) |
| **ERA5-Land** | **0.1°** (≈9 km), hourly | 1950– · days | Land variables: `2m_temperature`, `total_precipitation`, `skin_temperature`, `volumetric_soil_water_layer_1-4`, `total_evaporation`, `potential_evaporation`, `runoff`, `surface_runoff`, `snow_depth_water_equivalent`, `leaf_area_index_*` | CDS `reanalysis-era5-land`; GEE `ECMWF/ERA5_LAND/HOURLY` | CC-BY 4.0 |
| ERA5 time-series endpoint | 0.25°, point series | 1940– | Subset of variables, very fast for single points | CDS `reanalysis-era5-single-levels-timeseries` (labelled "experimental") [V] | CC-BY |
| E-OBS | 0.1°/0.25°, daily (station-gridded) | 1950– · 6-monthly versions | tg, tn, tx, rr, pp, hu, fg, qq | CDS `insitu-gridded-observations-europe` | **Own E-OBS licence; check commercial terms** [U] |
| **GPM IMERG V07** | 0.1°, 30-min / daily | 2000-06– · Early 4 h, Late 14 h, Final about 3.5 months | `precipitation` | `earthaccess` (`GPM_3IMERGHH`, `GPM_3IMERGDF`, `GPM_3IMERGDL`); GEE `NASA/GPM_L3/IMERG_V07` | NASA open. **V08 transition from fall 2026** [V] |
| **CHIRPS v3** | 0.05°, daily | 1981– | precip (60°N–60°S, which covers PL) | `data.chc.ucsb.edu/products/CHIRPS/v3.0/` [U path]; GEE `UCSB-CHC/CHIRPS/V3/DAILY_RNL` | Public domain |
| **EUMETNET OPERA radar composite** | 1–2 km, 15 min | 2011-10– | DBZH, RATE, ACRR (1 h accumulation) | MeteoGate API `https://api.meteogate.eu/eu-eumetnet-weather-radar`; ODIM HDF5 archive at CloudFerro [V] | Open (CC-BY [U]) |
| **IMGW-PIB stations (PL)** | ~57 synoptic + climate + precipitation stations | Daily/monthly archives from 1951; hourly current data; 10-min telemetry from 2008 | T, RH, wind, pressure, precipitation, snow; hydro: water level, discharge | REST `https://danepubliczne.imgw.pl/api/data/{synop,hydro,meteo}` + archive tree (§7.13) [V] | **Read the terms:** commercial use of basic-network data needs an agreement, *except* EU High-Value Datasets (meteo observations, radar), which are free. Get written confirmation [V]. |
| MSWEP V3 | 0.1°, hourly | 1979– | — | — | **CC BY-NC, commercial use needs a licence** |

Docs: [CDS how-to-api](https://cds.climate.copernicus.eu/how-to-api) · [ARCO-ERA5](https://github.com/google-research/arco-era5) · [Earthmover ERA5](https://docs.earthmover.io/sample-data/era5) · [GPM IMERG](https://gpm.nasa.gov/data/imerg) · [CHIRPS](https://www.chc.ucsb.edu/data) · [OPERA radar docs](https://eumetnet.github.io/openradardata-documentation/) · [IMGW API info](https://danepubliczne.imgw.pl/pl/apiinfo)

### 4.6 Hydrology, soil moisture, surface water

| Source | Res. | History | Access | Licence |
|---|---|---|---|---|
| **CLMS Surface Soil Moisture** (S1-based) | 1 km Europe, daily | 2014-10– | CDSE OData/S3 (`clms_ssm_europe_1km_daily_v1_nc` / `_cog`) [V] | Copernicus open |
| ESA CCI Soil Moisture v09.2 | 0.25°, daily | 1978-11 to 2024-12 | CEDA / CCI portal; CDS `satellite-soil-moisture` | CC-BY [U] |
| SMAP L4 `SPL4SMGP` | 9 km, 3-hourly (surface + root zone) | 2015-03– | `earthaccess` | NASA open. Geolocation issue 2026-05-14 to 07-28 being reprocessed [V] |
| GRACE/GRACE-FO JPL mascons RL06.3 | 0.5° grid (3° effective), monthly | 2002-04– | PO.DAAC via `earthaccess` | NASA open |
| GloFAS v5.0 (reanalysis) | 0.05°, daily discharge | 1979– | EWDS `cems-glofas-historical` (cdsapi, `url: https://ewds.climate.copernicus.eu/api`) [V] | CEMS-FLOODS licence [U] |
| EFAS historical v5.0 | ~1.5 km, 6-hourly/daily | 1992– | EWDS `efas-historical` | CEMS-FLOODS licence |
| European/Global Drought Observatory | ~1–5 km layers (SMI anomaly, fAPAR anomaly, SPI, CDI) | 2000s– | WMS `https://drought.emergency.copernicus.eu/api/wms`; file downloads [V] | EDO terms |
| **JRC Global Surface Water** v1.4 | 30 m | 1984–2021 (monthly history, occurrence, recurrence) | GEE `JRC/GSW1_4/*`; tiles from `global-surface-water.appspot.com` | Free, unrestricted |
| OPERA DSWx-HLS / DSWx-S1 | 30 m | 2023– (near-global) [V] | `earthaccess` (LP DAAC / PO.DAAC) | NASA open |
| HydroSHEDS v1 / HydroBASINS / HydroRIVERS | 3″–15″ | static | hydrosheds.org | Free incl. commercial (v1 licence) |
| EU-Hydro v1.3 | vector | 2020 | CLMS | Copernicus open |
| PL: MPHP10k (hydrographic division), JCWP/JCWPd water bodies, ISOK flood hazard/risk maps, PPSS drought maps | vector / WMS | cycles 2016–2022, 3rd cycle pending | Wody Polskie WMS + dane.gov.pl files (§4.13) [V] | CC BY 4.0 |

Primitives to pull: soil water (m³/m³), discharge (m³/s), terrestrial water storage anomaly (cm), water occurrence (%). Compute water masks yourself from S2 MNDWI/NDWI and S1 VV thresholds (§5).

### 4.7 Atmosphere and air quality

| Source | Res. | History · cadence | Primitives | Access | Licence |
|---|---|---|---|---|---|
| **Sentinel-5P TROPOMI L2** | 5.5 × 3.5 km (swath) | 2018-04– · daily | NO₂ tropospheric column, CO, O₃, SO₂, CH₄, HCHO, aerosol index, aerosol layer height, clouds | CDSE (STAC/OData/S3/openEO/Sentinel Hub; openEO and SH return one band per request) [V]; PC `sentinel-5p-l2-netcdf` [V]; **GEE L3 gridded** `COPERNICUS/S5P/OFFL/L3_NO2` etc. | Copernicus open |
| **CAMS European AQ reanalysis** | **0.1°, hourly**, surface + levels | **2013–2025** (interim → validated, about 1 year lag) [V] | NO₂, O₃, PM2.5, PM10, SO₂, CO, NH₃, NO, PANs, NMVOC, pollen (birch, grass, …) | ADS `cams-europe-air-quality-reanalyses` | CC-BY |
| CAMS European AQ forecasts | 0.1°, hourly, 4-day | **3-year rolling archive only, so archive it yourself** [V] | same | ADS `cams-europe-air-quality-forecasts` | CC-BY |
| CAMS global reanalysis EAC4 | 0.75°, 3-hourly | 2003–2025 | AOD, PM, NO₂, O₃, CO, … | ADS `cams-global-reanalysis-eac4` | CC-BY |
| **EEA AQ e-Reporting (in situ)** | stations | AirBase 2002–2012; E1a verified 2013–; E2a up-to-date | hourly/daily concentrations | [Download service](https://eeadmz1-downloads-webapp.azurewebsites.net/) (Parquet); Python `airbase` [V] | Open (CC-BY [U]) |
| **GIOŚ (PL)** | stations (PJP network) | archives 2000–2025 (annual xlsx); live API with recent data | PM10, PM2.5, NO₂, O₃, SO₂, CO, C₆H₆ | API `https://api.gios.gov.pl/pjp-api/v1/rest/…` (old API retired 2025-06-30) [V]; new **"Dane 3.0" API** `https://dane.gios.gov.pl/api/powietrze/v1/…` (CC BY 4.0, HVD) [V] | Open |
| OpenAQ v3 | stations, global | varies | — | `https://api.openaq.org/v3/` + `X-API-Key` (free; 60/min, 2,000/h) [V] | Varies per provider |
| WAQI | — | — | — | — | **Not allowed in commercial apps, so avoid** |

### 4.8 Noise

There is no EO modality for noise. You combine **regulatory model outputs** with **proxies** you compute yourself.

| Source | Content | Access | Licence |
|---|---|---|---|
| **GIOŚ noise API (PL, new 2026)** | `zasiegi-halasu` (GeoJSON noise contours from strategic maps), exposed population counts, measurements, limit values, action plans | `https://dane.gios.gov.pl/api/halas/v1/…` (no key) [V] | Licence not stated in metadata [U] |
| EEA END strategic noise maps | Round 3 (2017) exposure + contours (GDB via Eionet login), WMS `noise.discomap.eea.europa.eu`; round 4 (2022) via national/city portals | EEA SDI [V] | CC-BY 4.0 |
| City acoustic maps (PL) | Lden / Lnight rasters/contours per city; round 2022, next due 2027 [V] | dane.gov.pl (e.g. dataset 36854 Kielce), city geoportals | varies |
| **Proxies (derive)** | Distance to major roads/rail/airports; road class; **traffic AADT (GDDKiA GPR 2025 shapefile + XLSX)** [V]; building density/height (BDOT10k, NMPT, Overture) | OSM, Overture, GDDKiA, BDOT10k | ODbL / open |

To model noise from proxies, use the open-source **NoiseModelling** (CNOSSOS-EU implementation, Université Gustave Eiffel) with roads, traffic, buildings and DEM. Docs: [noise-planet.org/noisemodelling](https://noise-planet.org/noisemodelling.html) [U].

### 4.9 Population and socio-economics

| Source | Res. | Epochs | Access | Licence |
|---|---|---|---|---|
| **GHS-POP R2023A** (JRC) | 100 m / 1 km (Mollweide), 3″/30″ (WGS84) | 1975–2030, 5-year steps | `jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/`; GEE `JRC/GHSL/P2023A/GHS_POP` | CC-BY 4.0 [U] |
| **WorldPop Global2 R2025A** | 100 m (constrained) | **2015–2030 annual** ("alpha") [V] | REST `https://www.worldpop.org/rest/data/pop/wpgp?iso3=POL`; files `data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/` | CC-BY 4.0 |
| LandScan Global | 1 km (ambient population) | 2000–2024 annual | ORNL; GEE community `projects/sat-io/open-datasets/ORNL/LANDSCAN_GLOBAL` | **CC-BY 4.0** [V] |
| **Eurostat Census 2021 grid** | 1 km (EPSG:3035), 13 variables (sex, age bands, employed, place of birth, moved) | 2021 (2011 also) · version 2026-05-30 [V] | [GISCO population grids](https://ec.europa.eu/eurostat/en/web/gisco/geodata/population-distribution/population-grids) | Eurostat terms [U commercial] |
| **GUS NSP 2021 grid (PL)** | 1 km (sex/age) | 2021 | [geo.stat.gov.pl](https://geo.stat.gov.pl/app/cat/) (resource 19307, login to export) [V]; 250 m grid [U] | GUS terms |
| **GUS BDL (PL)** | Gmina/powiat/voivodeship statistics (thousands of variables: population, migration, water consumption, sewerage, economy, environment) | annual, 1995– | REST `https://bdl.stat.gov.pl/api/v1/` (optional `X-ClientId`) [V] | CC BY 4.0 |
| Kontur Population | H3 res 8 (~400 m) | 2023 | HDX | CC-BY |
| Meta HRSL | 30 m (1″) | ~2022 | `s3://dataforgood-fb-data/hrsl-cogs/` | CC-BY 4.0 |

Fusion tip: the population rasters are **epoch snapshots**. Interpolate linearly between epochs for a weekly batch and set `aggregation = "interpolated_epoch"`.

### 4.10 Land cover, land use, built-up, soil (static or annual)

| Source | Res. | Epochs | Access | Licence |
|---|---|---|---|---|
| ESA WorldCover | 10 m, 11 classes | 2020 (v100), 2021 (v200) | `s3://esa-worldcover` (COG + STAC); PC `esa-worldcover`; GEE `ESA/WorldCover/v200` | CC-BY 4.0 |
| Dynamic World | 10 m, per-scene class probabilities | 2015-06– near-real-time | **GEE only** `GOOGLE/DYNAMICWORLD/V1` | CC-BY 4.0, but commercial use requires paid GEE |
| Impact Observatory LULC | 10 m, 9 classes | 2017–2023 annual | PC `io-lulc-annual-v02` [V] | CC-BY 4.0 |
| CLMS CORINE Land Cover | 100 m / MMU 25 ha | 1990, 2000, 2006, 2012, **2018** (2024 in production [U]) | CLMS portal/API; moving to CDSE by end of 2026 [V] | Copernicus open |
| CLMS HRL Imperviousness | **10 m** | **2024 released 2026-06** [V], plus earlier epochs | CLMS / CDSE | open |
| CLMS HRL Tree Cover, Grassland, Water & Wetness, CLCplus Backbone 2023, Urban Atlas 2021, HR-VPP phenology | 10 m–20 m | various | CLMS download API (EU Login → service key → JWT) [V] | open |
| Hansen Global Forest Change | 30 m | 2000–2025 (v1.13) [V] | GEE `UMD/hansen/global_forest_change_2025_v1_13` [U id]; tiles on GCS | CC-BY 4.0 |
| SoilGrids 2.0 | 250 m, 6 depths (clay, sand, silt, SOC, pH, bulk density, …) | static | **WCS** `https://maps.isric.org/` (the REST API is paused) [V] | CC-BY 4.0 |
| Building footprints | vector | — | **Overture** (release `2026-09-23.1`; schema v2 breaking changes) [V]; **Microsoft ML buildings** (covers PL; refreshed 2026-08) [V]; BDOT10k `BUBD_A` | ODbL / CDLA-P-2.0 |
| OpenStreetMap | vector | continuous; full history available | Geofabrik `poland-latest.osm.pbf`; Overpass; `quackosm`, `pyrosm`, `osmnx` | ODbL (share-alike on *derived databases*; check before redistributing) |

### 4.11 Learned embeddings — the most compact multi-modal summary available

If "reduced form" means "as compact as possible while still useful for ML", per-pixel foundation-model embeddings are the strongest option. They are an *annual* summary, so they cannot replace weekly primitives.

| Dataset | Res. · dims | Years | Access | Licence |
|---|---|---|---|---|
| **Google Satellite Embedding (AlphaEarth Foundations)** | 10 m · 64-d (unit length) | 2017–2024 in the EE catalogue; GCS readme says 2017–2025 [V] | GEE `GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL`; GCS COGs `gs://alphaearth_foundations/satellite_embedding/v1/annual/` (int8, **provider-pays since 2026-07**); **Source Cooperative mirror** `s3://us-west-2.opendata.source.coop/tge-labs/aef` (free) [V] | **CC-BY 4.0, commercial OK** |
| TESSERA | 10 m · 128-d | 2017–2025 | `s3://tessera-embeddings/v1.1/dclimate.icechunk`; `geotessera` | **CC-BY-SA** (share-alike) |
| Major TOM | patch-level (SigLIP, DINOv2, SSL4EO…) | — | Hugging Face `Major-TOM` | [U] |
| Clay | patch-level | — | Source Cooperative `clay` | [U] |

### 4.12 Ground motion → see EGMS in §4.2.

### 4.13 Poland — national geodetic, cadastral, environmental services

All GUGiK services: CRS **EPSG:2180**. The licence is free and covers any use, including commercial [V]. Base path `https://mapy.geoportal.gov.pl/wss/service/PZGIK/…` (older `integracja.gugik.gov.pl/cgi-bin/*` services moved here in 2022).

| Dataset | Endpoint(s) | Notes |
|---|---|---|
| **NMT 1 m (DTM)** | WCS `…/PZGIK/NMT/GRID1/WCS/DigitalTerrainModelFormatTIFF` (GeoTIFF) and `…/DigitalTerrainModel` (ASCII Grid) [V] | Run `DescribeCoverage` first; the two may use different height datums [U]. Point heights: `https://services.gugik.gov.pl/nmt/?request=GetHByPointList&list=…` (≤500 pts, Y X order) [U] |
| NMT sheet index (bulk) | WFS `…/PZGIK/NumerycznyModelTerenuEVRF2007/WFS/Skorowidze` [V] | Each feature carries a download URL, so you can script a bulk download per AOI |
| NMPT (DSM) | WCS `…/PZGIK/NMPT/GRID1/WCS/DigitalSurfaceModel` [V] | 0.5 m in cities |
| LiDAR LAS/LAZ | WFS `…/PZGIK/DanePomiaroweLidarEVRF2007/WFS/Skorowidze` (2018–2025), `…KRON86…` (2010–2019) [V] | Process with PDAL |
| Orthophoto | WMTS/WMS `…/PZGIK/ORTO/{WMS,WMTS}/{StandardResolution,HighResolution}`; WCS `…/ORTO/WCS/{StandardResolution,HighResolution,TrueOrto}`; index WFS `…/ORTO/WFS/Skorowidze` [V] | 5–25 cm RGB/CIR; update cycle about 2 years (urban) to 5 years (rural). An index sheet's date gives the epoch. |
| **BDOT10k** (topographic vector, 1:10k) | **GeoParquet/GPKG per object class**: `https://opendata.geoportal.gov.pl/bdot10k/schemat2021/GeoParquet/<CLASS>.parquet` (e.g. `OT_ADJA_A`) [V, new 2026-04]; per-powiat GML zips [U pattern]; WMTS view | Classes: `OT_PTWP_A` (surface water), `OT_SWRS_L` (rivers), `OT_BUBD_A` (buildings), `OT_SKDR_L` (roads), `OT_PTLZ_A` (forest), … |
| **PRG** (admin boundaries, address points) | `https://opendata.geoportal.gov.pl/prg/granice/00_jednostki_administracyjne.zip`; `…/prg/adresy/PunktyAdresowe/POLSKA.zip` [V]; **OGC API Features** `https://ogcapi.geoportal.gov.pl/res/ims/maps/PRG/apiFeatures` [V] | Updated yearly (as of 1 Jan) |
| **EGiB parcels** | **ULDK** `https://uldk.gugik.gov.pl/?request=GetParcelByXY&xy=…,2180&result=geom_wkt,teryt` (also `GetParcelById`, `GetBuildingByXY`, …) [V]; KIEG WMS; aggregate WFS `…/PZGIK/EGIB/WFS/UslugaZbiorcza` [V] | Quality varies across about 380 county sources |
| Geocoder (UUG) | `https://services.gugik.gov.pl/uug/?request=GetAddress&address=…` [U] | Returns EPSG:2180 |
| Endpoint reference implementation | Official QGIS plugin source [github.com/GlownyUrzadGeodezjiIKartografii/pobierzdaneGUGiK](https://github.com/GlownyUrzadGeodezjiIKartografii/pobierzdaneGUGiK) [V] | **Read this repo before writing GUGiK clients.** It holds the canonical URLs. |
| **PIG-PIB geology** | SOPO landslides WMS `https://cbdgmapa.pgi.gov.pl/arcgis/services/geozagrozenia/sopo_obszary/MapServer/WMSServer`; boreholes WFS `…/hydrogeologia/cbdh_otwory/MapServer/WFSServer`; INSPIRE geology WMS; [gis.pgi.gov.pl](https://gis.pgi.gov.pl) [V] | MhP (hydrogeological map) access conditions are governed by PIG order 3/2026 and may be paid [U]. PSH groundwater monitoring is on request. |
| **Wody Polskie** | Flood hazard WMS `https://wody.isok.gov.pl/gpservices/KZGW/MZP20_{Glebokosc,Predkosc}_{Niskie,Srednie,Wysokie}PrawdopodPowodzi/MapServer/WMSServer`; MPHP WMS `…/KZGW/ISOK_MPHP/MapServer/WMSServer`; JCWP WMS `…/KZGW/ISOK_PGW/…`; flood extent SHP and PPSS drought files via dane.gov.pl [V] | CC BY 4.0 |
| **GDOŚ** protected areas | WMS `https://sdi.gdos.gov.pl/wms`, WFS `https://sdi.gdos.gov.pl/wfs` (`TYPENAME=GDOS:{SpecjalneObszaryOchrony,ObszarySpecjalnejOchrony,ParkiNarodowe,Rezerwaty,…}`, `outputFormat=shape-zip`) [V] | Data licence not stated [U] |
| **ARiMR LPIS/GSA** (agricultural parcels, declared crops) | Opened 2025-12-11; geoportal.arimr.gov.pl (WMS/WFS/SHP) [V], exact endpoints [U]; legacy WMS `https://mapy.geoportal.gov.pl/wss/ext/arimr_lpis` [V] | Free, commercial OK, anonymised |
| GDDKiA GPR 2025 (traffic counts) | XLSX tables + `2025_DK_OdcPom_Web.zip` shapefile at [gov.pl/web/gddkia/generalny-pomiar-ruchu-2025](https://www.gov.pl/web/gddkia/generalny-pomiar-ruchu-2025) [V] | 5-year cycle |
| GUS TERYT | SOAP (account by email); full files on dane.gov.pl dataset 58 [V] | CC BY |
| **dane.gov.pl** (national open-data portal) | `https://api.dane.gov.pl/1.4/` (datasets, resources; JSON:API; no key) [V] | Use it to discover new resources programmatically |
| Sat4Envi | Dormant since 2022, no API [V] | Do not depend on it |
| CREODIAS / CloudFerro | Operator of CDSE infrastructure in Warsaw | Low-latency compute next to the Copernicus archive (paid VMs) |

---

## 5. Derived indicators — compute from primitives

Use **[spyndex](https://spyndex.readthedocs.io)** (the Awesome Spectral Indices catalogue, 200+ indices, works on xarray/dask) instead of hand-coding. The table below lists the core set with formulas, so you can validate results.

### 5.1 Band mapping (spyndex symbols)

| Symbol | Meaning | Sentinel-2 | Landsat 8/9 (C2 L2) | Landsat 4/5/7 | HLS (L30/S30) |
|---|---|---|---|---|---|
| `B` | blue | B02 | SR_B2 | SR_B1 | B02 / B02 |
| `G` | green | B03 | SR_B3 | SR_B2 | B03 / B03 |
| `R` | red | B04 | SR_B4 | SR_B3 | B04 / B04 |
| `RE1` | red-edge 1 | B05 | — | — | — / B05 |
| `N` | NIR (broad) | B08 | SR_B5 | SR_B4 | B05 / B08 |
| `N2` | NIR narrow | B8A | (SR_B5) | — | — / B8A |
| `S1` | SWIR 1.6 µm | B11 | SR_B6 | SR_B5 | B06 / B11 |
| `S2` | SWIR 2.2 µm | B12 | SR_B7 | SR_B7 | B07 / B12 |
| `T` | thermal | — | ST_B10 | ST_B6 | — |

Note that HLS band numbers differ between L30 and S30. Always map by common name.

### 5.2 Core spectral indices

| Index | Formula | Use |
|---|---|---|
| NDVI | (N − R) / (N + R) | Vegetation vigour |
| EVI | 2.5 (N − R) / (N + 6R − 7.5B + 1) | Dense canopy; less saturation than NDVI |
| SAVI | 1.5 (N − R) / (N + R + 0.5) | Sparse vegetation, soil background |
| NDRE | (N2 − RE1) / (N2 + RE1) | Chlorophyll, nitrogen (S2 only) |
| **NDWI** (McFeeters) | (G − N) / (G + N) | Open water |
| **MNDWI** | (G − S1) / (G + S1) | Open water, suppresses built-up (same formula as NDSI snow) |
| **NDMI** (Gao NDWI) | (N − S1) / (N + S1) | Vegetation and canopy water content |
| MSI | S1 / N | Moisture stress |
| NBR / dNBR | (N − S2) / (N + S2); dNBR = NBR_pre − NBR_post | Burn severity, disturbance |
| NDBI | (S1 − N) / (S1 + N) | Built-up |
| BSI | ((S1 + R) − (N + B)) / ((S1 + R) + (N + B)) | Bare soil |
| AWEI_nsh | 4 (G − S1) − (0.25 N + 2.75 S2) | Water without shadow confusion |

Valid data: mask first (SCL / QA_PIXEL), then compute, then aggregate in time. **Never take the median of an index computed on unmasked pixels.**

### 5.3 SAR-derived

| Indicator | Formula | Notes |
|---|---|---|
| Backscatter in dB | σ⁰_dB = 10 · log10(σ⁰_lin) | **Aggregate in linear power, convert to dB afterwards** |
| Cross-pol ratio | VH_dB − VV_dB | Vegetation structure, crop stage |
| Dual-pol RVI | 4 · VH / (VV + VH) (linear) | Radar vegetation index |
| Water mask | VV_dB < threshold (≈ −18 dB start; better Otsu per scene) | Flooding, permanent water; complements MNDWI under cloud |
| Change | log-ratio = 10 · log10(σ⁰_t2 / σ⁰_t1) | Disturbance, flood onset |
| Soil moisture proxy | Change detection (TU Wien approach) or use CLMS SSM directly | |

### 5.4 Meteorological / hydrological derivations

Compute these with **[xclim](https://xclim.readthedocs.io)** (CF-aware climate indices) or the formulas below.

| Indicator | Inputs | Formula / function |
|---|---|---|
| Wind speed | u10, v10 | √(u² + v²), computed **hourly before** temporal aggregation |
| Saturation vapour pressure | T (°C) | e_s = 0.6108 · exp(17.27 T / (T + 237.3)) kPa |
| Actual vapour pressure | Td (°C) | e_a = same formula with Td |
| VPD | T, Td | e_s − e_a |
| Relative humidity | T, Td | 100 · e_a / e_s |
| PET | T, radiation, wind, humidity | `xclim.indices.potential_evapotranspiration(method="PM"/"HG"/"BR65")`, or ERA5-Land `potential_evaporation` |
| SPI / SPEI (1, 3, 6, 12 months) | precipitation (minus PET for SPEI), ≥30-year baseline | `xclim.indices.standardized_precipitation_index`, `…_evapotranspiration_index`. Needs a long ERA5/ERA5-Land/CHIRPS history. |
| Climatic water balance | P − PET | weekly sum |
| GDD | Tmean | Σ max(0, T − T_base) |
| Heat stress days | Tmax | count(Tmax > threshold) |
| ERA5 unit traps | — | `total_precipitation` is in **m** (×1000 → mm). ERA5-Land accumulations are **cumulative since 00 UTC** (difference consecutive hours; the 00 UTC value holds the previous day's total). ERA5 hourly accumulations are per hour. Temperatures are in K. |

### 5.5 Terrain derivatives (from DEM)

Slope, aspect, curvature, hillshade (`xarray-spatial`); flow direction/accumulation, catchments, HAND (`pysheds`, `whitebox`); **TWI = ln(a / tan β)** (a = specific catchment area, β = slope); TPI/TRI (`richdem`, `whitebox`); depressions/sinks (`whitebox` `FillDepressions`, difference with the original DEM).

### 5.6 Atmosphere

- S5P quality filter: **NO₂ `qa_value > 0.75`**; most other products `> 0.5`. Then grid to a fixed lat/lon or UTM grid. Use the GEE L3 products, or `harp` / `xESMF` / simple binning yourself.
- European AQI from concentrations (PM2.5, PM10, NO₂, O₃, SO₂): use the EEA EAQI band thresholds, applied per pollutant; overall = worst band.

---

## 6. Temporal batching — weekly (or any cadence) batches

### 6.1 Week definition

- Work in **UTC**. Define the batch as `[start, end)` with `start` = **Monday 00:00 UTC** (ISO week).
- Use `ds.resample(time="7D", origin=pd.Timestamp(start))`. This is left-closed and left-labelled by default, so it avoids pandas `"W"` anchoring surprises (`"W"` = `W-SUN`, right-labelled).
- Station sources may report local time (GIOŚ: Europe/Warsaw [U]). Convert to UTC in the adapter.

### 6.2 Aggregation rules per modality

| Modality | Native cadence | Weekly reducer | Extra outputs |
|---|---|---|---|
| Optical reflectance (S2, Landsat, HLS) | 2–16 days, cloud-gappy | **Masked median per band**, or a medoid / max-NDVI composite | `valid_obs` (count), `valid_fraction`, `doy_of_obs` |
| Spectral indices | derived | compute per scene **after masking**, then median | p10/p90 optional |
| SAR backscatter | 6–12 days | **Mean in linear power**, then dB | `n_acq`, orbit direction(s) |
| LST | daily–16 days | mean of valid day observations (separate day and night) | count |
| Precipitation | 30 min–daily | **sum** (mm/week) | max daily, wet days |
| Temperature | hourly | mean, min, max | GDD, VPD mean |
| Radiation | hourly | sum (MJ/m²) or mean (W/m²) | |
| Wind | hourly | mean speed (from hourly speed), max gust | |
| Soil moisture | daily–3 h | mean | anomaly vs climatology |
| Discharge | daily | mean, max | |
| S5P columns | daily | mean of QA-filtered | count |
| CAMS concentrations | hourly | mean, p95, (O₃: max daily 8-h mean) | |
| Station AQ / meteo | hourly/daily | as above, per station; keep the `station` dimension | completeness % |
| DEM, soil, land cover | static / annual | no time dim, or `time` = epoch year | `epoch` attr |
| Population | 1–5-year epochs | linear interpolation between epochs | `aggregation="interpolated_epoch"` |
| Embeddings | annual | select the year containing the week | |
| Ground motion (EGMS) | 6–12 day series, annual release | mean velocity, or displacement at the nearest dates | release version |

### 6.3 Latency (how fresh a "this week" batch can be)

| Source | Typical latency |
|---|---|
| Sentinel-2 L2A | hours to 1 day |
| Sentinel-1 GRD | hours; PC RTC ~1–2 days |
| Landsat C2 L2 | ~1–3 weeks for T1 L2 (real-time L1 ~12 h) [U] |
| HLS | ~1.7 days [V] |
| S5P | NRT ~3 h; OFFL ~ days |
| ERA5 (CDS) | ~5 days (ERA5T); ARCO/Earthmover mirrors: 1 week to 3 months |
| ERA5-Land | ~5 days [U] |
| IMERG | Early 4 h / Late 14 h / Final ~3.5 months |
| CAMS EU reanalysis | ~1 year (use forecast analyses for recent weeks) |
| IMGW synop | hourly |
| GIOŚ live API | hourly (live endpoint holds only recent days; history comes from the archives) |
| EGMS | annual |

**Design implication:** every variable carries a `status` attribute (`final`, `preliminary`, `missing`), so the same week can be recomputed later when final products arrive (ERA5T → ERA5, IMERG Early → Final).

---

## 7. Code recipes

All recipes assume these shared variables:

```python
import os, numpy as np, pandas as pd, xarray as xr
from shapely.geometry import shape

AOI = {
    "type": "Polygon",
    "coordinates": [[[16.90, 51.05], [17.15, 51.05], [17.15, 51.18], [16.90, 51.18], [16.90, 51.05]]],
}  # Wrocław
BBOX = shape(AOI).bounds  # (minx, miny, maxx, maxy) EPSG:4326
START, END = "2024-06-03", "2024-07-01"  # [start, end); START is a Monday
STAC_DT = f"{START}T00:00:00Z/{(pd.Timestamp(END) - pd.Timedelta(seconds=1)):%Y-%m-%dT%H:%M:%SZ}"
WEEK = dict(time="7D", origin=pd.Timestamp(START))  # use as ds.resample(**WEEK)
```

### 7.0 Environment

```bash
python -m pip install \
  "pystac-client>=0.9" "odc-stac>=0.5" planetary-computer "rioxarray>=0.17" "xarray>=2025.1" \
  "zarr>=3" "dask[distributed]" rasterio shapely pyproj geopandas pyarrow \
  spyndex xclim xarray-spatial \
  earthaccess "cdsapi>=0.7.7" openeo sentinelhub \
  earthengine-api "xee>=0.1" geemap \
  gcsfs s3fs icechunk h5netcdf owslib requests \
  quackosm overturemaps airbase
```

Versions checked on PyPI on 2026-10-06 [V]: pystac-client 0.9.0, odc-stac 0.5.3, xarray 2026.9.0, zarr 3.4.0, rasterio 1.5.2, GDAL 3.13.3, earthaccess 0.19.0, openeo 0.52.0, sentinelhub 3.12.0, xee 0.1.2, earthengine-api 1.7.47, spyndex 0.12.0. `stackstac` (0.5.1, 2024) is still usable, but **prefer `odc-stac`**, which is actively maintained.

GDAL settings for streaming COGs over HTTP:

```python
os.environ.update(
    {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "GDAL_HTTP_MAX_RETRY": "5",
        "GDAL_HTTP_RETRY_DELAY": "1",
        "VSI_CACHE": "TRUE",
        "GDAL_CACHEMAX": "512",
    }
)
import odc.stac

odc.stac.configure_rio(cloud_defaults=True)
```

### 7.1 Sentinel-2 L2A weekly composite + indices (Earth Search, no auth)

```python
import pystac_client, odc.stac, spyndex

cat = pystac_client.Client.open("https://earth-search.aws.element84.com/v1")
items = cat.search(
    collections=["sentinel-2-c1-l2a"], bbox=BBOX, datetime=STAC_DT,
    query={"eo:cloud_cover": {"lt": 60}},
).item_collection()

BANDS = ["blue", "green", "red", "rededge1", "nir", "nir08", "swir16", "swir22"]
ds = odc.stac.load(
    items, bands=BANDS + ["scl"], bbox=BBOX, crs="utm", resolution=10,
    chunks={"x": 2048, "y": 2048}, groupby="solar_day",
    resampling={"scl": "nearest", "*": "bilinear"},
)

# scale/offset from STAC metadata (sentinel-2-c1-l2a: scale 0.0001, offset -0.1)
def scale_offset(item, asset):
    rb = item.assets[asset].extra_fields.get("raster:bands", [{}])[0]
    return rb.get("scale", 1.0), rb.get("offset", 0.0)


valid = ds.scl.isin([4, 5, 6, 7, 11])
refl = xr.Dataset(
    {
        b: (ds[b].where(valid & (ds[b] > 0)) * scale_offset(items[0], b)[0] + scale_offset(items[0], b)[1])
        for b in BANDS
    }
)

idx = spyndex.computeIndex(
    index=["NDVI", "EVI", "NDWI", "MNDWI", "NDMI", "NBR", "NDBI"],
    params={
        "B": refl.blue,
        "G": refl.green,
        "R": refl.red,
        "N": refl.nir,
        "S1": refl.swir16,
        "S2": refl.swir22,
        "L": 1.0,
        "g": 2.5,
        "C1": 6.0,
        "C2": 7.5,
    },
)  # DataArray with dim "index"

stack = xr.merge([refl, idx.to_dataset(dim="index")]).chunk(time=-1)
s2_weekly = stack.resample(**WEEK).median()
s2_weekly["valid_obs"] = valid.resample(**WEEK).sum()
s2_weekly.attrs.update(
    source="Earth Search",
    collection="sentinel-2-c1-l2a",
    licence="Copernicus open data",
    attribution="Contains modified Copernicus Sentinel data 2024",
)
```

Docs: [odc-stac](https://odc-stac.readthedocs.io) · [pystac-client](https://pystac-client.readthedocs.io) · [spyndex](https://spyndex.readthedocs.io)

### 7.2 Landsat Collection 2 L2 (Planetary Computer) — long history + LST

```python
import planetary_computer as pc, pystac_client, odc.stac

cat = pystac_client.Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace
)
items = cat.search(
    collections=["landsat-c2-l2"],
    bbox=BBOX,
    datetime="1995-05-01/1995-09-30",
    query={
        "eo:cloud_cover": {"lt": 50},
        "platform": {"in": ["landsat-5", "landsat-7", "landsat-8", "landsat-9"]},
    },
).item_collection()

ls = odc.stac.load(
    items,
    bands=["blue", "green", "red", "nir08", "swir16", "swir22", "qa_pixel"],
    bbox=BBOX,
    crs="utm",
    resolution=30,
    chunks={"x": 2048, "y": 2048},
    groupby="solar_day",
)
qa = ls.qa_pixel.astype("uint16")
bad = (qa & 0b11111) > 0  # bits 0-4: fill, dilated cloud, cirrus, cloud, shadow
sr = ls.drop_vars("qa_pixel").where(~bad) * 0.0000275 - 0.2
# Thermal: asset "lwir11" (L8/9) or "lwir" (L4-7) [U: confirm per item]; ST_K = DN * 0.00341802 + 149.0
```

SAS tokens expire (~1 h). For long jobs, sign lazily: `pc.sign(item)` right before loading, or set `PC_SDK_SUBSCRIPTION_KEY`. Docs: [PC Landsat C2 L2](https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2) · [PC data access](https://planetarycomputer.microsoft.com/docs/concepts/sas/)

### 7.3 Sentinel-1 RTC weekly backscatter (Planetary Computer)

```python
items = cat.search(
    collections=["sentinel-1-rtc"],
    bbox=BBOX,
    datetime=STAC_DT,
    query={"sat:orbit_state": {"eq": "ascending"}},
).item_collection()
s1 = odc.stac.load(
    items,
    bands=["vv", "vh"],
    bbox=BBOX,
    crs="utm",
    resolution=10,
    chunks={"x": 2048, "y": 2048},
    groupby="solar_day",
)
s1 = s1.where(s1 > 0).chunk(time=-1)
lin = s1.resample(**WEEK).mean()  # average in LINEAR power
s1_weekly = xr.Dataset(
    {
        "vv_db": 10 * np.log10(lin.vv),
        "vh_db": 10 * np.log10(lin.vh),
        "cr_db": 10 * np.log10(lin.vh / lin.vv),
        "rvi": 4 * lin.vh / (lin.vv + lin.vh),
        "n_acq": s1.vv.notnull().resample(**WEEK).sum(),
    }
)
```

Keep a single orbit direction per series. Mixing ascending and descending passes changes the incidence geometry.

### 7.4 CDSE STAC + S3 (full Copernicus archive, EU-hosted)

Asset hrefs are `s3://eodata/...` (JP2 for S2, not COG). Each item carries `raster:bands` scale 0.0001 / offset −0.1 and the processing baseline in the product ID (e.g. `N0513`) [V].

```python
os.environ.update(
    {
        "AWS_S3_ENDPOINT": "eodata.dataspace.copernicus.eu",
        "AWS_ACCESS_KEY_ID": os.environ[
            "CDSE_S3_KEY"
        ],  # from https://eodata-s3keysmanager.dataspace.copernicus.eu/
        "AWS_SECRET_ACCESS_KEY": os.environ["CDSE_S3_SECRET"],
        "AWS_VIRTUAL_HOSTING": "FALSE",
        "AWS_HTTPS": "YES",
        "AWS_REGION": "default",
    }
)
cat = pystac_client.Client.open("https://stac.dataspace.copernicus.eu/v1/")
items = cat.search(
    collections=["sentinel-2-l2a"],
    bbox=BBOX,
    datetime=STAC_DT,
    filter={"op": "<", "args": [{"property": "eo:cloud_cover"}, 60]},
    filter_lang="cql2-json",
).item_collection()
ds = odc.stac.load(
    items,
    bands=["B04_10m", "B08_10m", "SCL_20m"],
    bbox=BBOX,
    crs="utm",
    resolution=10,
    chunks={"x": 2048, "y": 2048},
    groupby="solar_day",
)
```

Quota: 4 parallel connections × 20 MB/s and 12 TB per 30 days [V]. Limit dask to about 4 concurrent reads (`dask.config.set(scheduler="threads", num_workers=4)`). For large AOIs, use server-side aggregation (7.5 / 7.6). Docs: [CDSE STAC](https://documentation.dataspace.copernicus.eu/APIs/STAC.html) · [CDSE S3](https://documentation.dataspace.copernicus.eu/APIs/S3.html) · [Quotas](https://documentation.dataspace.copernicus.eu/Quotas.html)

### 7.5 openEO on CDSE — server-side weekly NDVI time series per polygon

```python
import openeo

conn = openeo.connect("openeo.dataspace.copernicus.eu").authenticate_oidc()
cube = conn.load_collection(
    "SENTINEL2_L2A",
    spatial_extent=dict(zip(["west", "south", "east", "north"], BBOX)),
    temporal_extent=[START, END],
    bands=["B04", "B08", "SCL"],
    max_cloud_cover=60,
)
scl = cube.band("SCL")
cloud = ~((scl == 4) | (scl == 5) | (scl == 6))
ndvi = cube.filter_bands(["B04", "B08"]).mask(cloud).ndvi(red="B04", nir="B08")
weekly = ndvi.aggregate_temporal_period(period="week", reducer="median")

weekly.download("ndvi_weekly.nc")  # raster cube (small AOI)
weekly.aggregate_spatial(geometries=AOI, reducer="mean").download("ndvi_ts.json")  # time series
# large jobs: job = weekly.execute_batch(out_format="GTiff")  -> COGs per date
```

Free tier: 10,000 credits/month, 2 concurrent batch jobs [V]. Docs: [openEO Python client](https://open-eo.github.io/openeo-python-client/) · [CDSE openEO](https://documentation.dataspace.copernicus.eu/APIs/openEO/openEO.html)

### 7.6 Sentinel Hub Statistical API on CDSE — aggregates without pixels

```python
from sentinelhub import SHConfig, SentinelHubStatistical, DataCollection, Geometry, CRS

cfg = SHConfig()
cfg.sh_client_id, cfg.sh_client_secret = os.environ["SH_CLIENT_ID"], os.environ["SH_CLIENT_SECRET"]
cfg.sh_base_url = "https://sh.dataspace.copernicus.eu"
cfg.sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
S2 = DataCollection.SENTINEL2_L2A.define_from("s2l2a_cdse", service_url=cfg.sh_base_url)

EVALSCRIPT = """//VERSION=3
function setup() {
  return {input: [{bands: ["B04", "B08", "B11", "SCL", "dataMask"]}],
          output: [{id: "idx", bands: 2, sampleType: "FLOAT32"}, {id: "dataMask", bands: 1}]};
}
function evaluatePixel(s) {
  const ok = [4, 5, 6].includes(s.SCL) ? 1 : 0;
  return {idx: [(s.B08 - s.B04) / (s.B08 + s.B04), (s.B08 - s.B11) / (s.B08 + s.B11)],
          dataMask: [s.dataMask * ok]};
}"""
geom = Geometry(AOI, crs=CRS.WGS84).transform(CRS.UTM_33N)  # resolution is in CRS units -> metres
req = SentinelHubStatistical(
    aggregation=SentinelHubStatistical.aggregation(
        evalscript=EVALSCRIPT, time_interval=(START, END), aggregation_interval="P7D", resolution=(10, 10)
    ),
    input_data=[SentinelHubStatistical.input_data(S2)],
    geometry=geom,
    config=cfg,
)
stats = req.get_data()[0]  # per-week mean/std/percentiles of NDVI, NDMI
```

Free tier: 10k requests and 10k PU per month [V]. API paths were migrated to `/statistics/v1` etc. in March 2026; the old `/api/v1` paths still work [V]. Docs: [sentinelhub-py](https://sentinelhub-py.readthedocs.io) · [CDSE Sentinel Hub](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub.html)

### 7.7 ERA5 from ARCO Zarr (no queue) → weekly meteorology

```python
era = xr.open_zarr("gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3",
                   chunks={}, storage_options={"token": "anon"})
minx, miny, maxx, maxy = BBOX
sub = era[["2m_temperature", "2m_dewpoint_temperature", "total_precipitation",
           "10m_u_component_of_wind", "10m_v_component_of_wind",
           "surface_solar_radiation_downwards"]].sel(
    time=slice(START, pd.Timestamp(END) - pd.Timedelta(hours=1)),
    latitude=slice(maxy + 0.25, miny - 0.25),          # latitude is descending
    longitude=slice(minx - 0.25, maxx + 0.25))          # 0..360; for western lons use lon % 360

t = sub["2m_temperature"] - 273.15
td = sub["2m_dewpoint_temperature"] - 273.15
es = 0.6108 * np.exp(17.27 * t / (t + 237.3)); ea = 0.6108 * np.exp(17.27 * td / (td + 237.3))
wind = np.hypot(sub["10m_u_component_of_wind"], sub["10m_v_component_of_wind"])
era_weekly = xr.Dataset({
    "t2m_mean": t.resample(**WEEK).mean(), "t2m_min": t.resample(**WEEK).min(), "t2m_max": t.resample(**WEEK).max(),
    "precip_mm": (sub["total_precipitation"] * 1000).resample(**WEEK).sum(),
    "vpd_kpa": (es - ea).resample(**WEEK).mean(),
    "wind_ms": wind.resample(**WEEK).mean(),
    "ssrd_mj": (sub["surface_solar_radiation_downwards"] / 1e6).resample(**WEEK).sum(),
})
```

Check `era.attrs` for `valid_time_stop` / `valid_time_stop_era5t` to see how fresh the store is [V]. Alternative store (Icechunk, anonymous, us-east-1): `icechunk.s3_storage(bucket="earthmover-icechunk-era5", prefix="icechunkV2", region="us-east-1", anonymous=True)` → `Repository.open(...)` → `readonly_session("main").store`. See the [Earthmover docs](https://docs.earthmover.io/sample-data/era5) for group names [U].

### 7.8 CDS / ADS API — ERA5-Land and CAMS European reanalysis

`~/.cdsapirc` (CDS) — one personal token, no UID prefix since the 2024 CDS migration [V]:

```
url: https://cds.climate.copernicus.eu/api
key: <PERSONAL-ACCESS-TOKEN>
```

```python
import cdsapi
days = [f"{d:02d}" for d in range(1, 32)]
cdsapi.Client().retrieve("reanalysis-era5-land", {
    "variable": ["2m_temperature", "total_precipitation", "volumetric_soil_water_layer_1",
                 "potential_evaporation", "skin_temperature"],
    "year": "2024", "month": ["06"], "day": days, "time": [f"{h:02d}:00" for h in range(24)],
    "area": [BBOX[3], BBOX[0], BBOX[1], BBOX[2]],          # N, W, S, E
    "data_format": "netcdf", "download_format": "unarchived",
}, "era5land_202406.nc")

# CAMS (ADS) — separate client/url/token
ads = cdsapi.Client(url="https://ads.atmosphere.copernicus.eu/api", key=os.environ["ADS_KEY"])
ads.retrieve("cams-europe-air-quality-reanalyses", {
    "variable": ["nitrogen_dioxide", "particulate_matter_2.5um", "ozone"],
    "model": ["ensemble"], "level": ["0"], "type": ["validated_reanalysis"],
    "year": ["2022"], "month": ["06"],
}, "cams_eu_202206.zip")
```

**Always generate the exact request body from the dataset page's "Download → Show API request" panel.** Parameter names differ between datasets [U for the CAMS keys above]. Accept each dataset's licence on its web page once, or the API returns 403. Remember that ERA5-Land accumulations are cumulative since 00 UTC (§5.4). Docs: [CDS API](https://cds.climate.copernicus.eu/how-to-api) · [ADS API](https://ads.atmosphere.copernicus.eu/how-to-api) · [EWDS](https://ewds.climate.copernicus.eu)

### 7.9 Google Earth Engine — read with xee, export COG, import your own COG

```python
import ee, xarray as xr
from xee import helpers  # xee >= 0.1 (breaking API change vs 0.0.x)

ee.Initialize(project="my-gcp-project", opt_url="https://earthengine-highvolume.googleapis.com")
region = ee.Geometry(AOI)

# (a) Read: Satellite Embedding (AlphaEarth), 64-d at 10 m, one image per year
ic = (
    ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
    .filterBounds(region)
    .filterDate("2023-01-01", "2024-01-01")
)
grid = helpers.fit_geometry(shape(AOI), grid_crs="EPSG:32633", grid_scale=(10, -10))  # [V] signature
emb = xr.open_dataset(ic, engine="ee", **grid)  # dims: time, x, y; vars A00..A63

# (b) Read: Dynamic World class probabilities, weekly mean (server-side reduce first)
dw = ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1").filterBounds(region).filterDate(START, END)

# (c) Export a server-side result as a Cloud-Optimized GeoTIFF to GCS
img = dw.select(["water", "trees", "built"]).mean().clip(region)
ee.batch.Export.image.toCloudStorage(
    image=img,
    description="dw_mean",
    bucket="my-bucket",
    fileNamePrefix="gee/dw_mean_2024w23",
    region=region,
    scale=10,
    crs="EPSG:32633",
    fileFormat="GeoTIFF",
    formatOptions={"cloudOptimized": True},
    maxPixels=1e10,
).start()

# (d) Feed your own fusion COG into GEE without ingestion: register a COG-backed asset
ee.data.createAsset(
    {
        "type": "IMAGE",
        "gcs_location": {"uris": ["gs://my-bucket/fusion/lst_2024-06-03.tif"]},
        "startTime": "2024-06-03T00:00:00Z",
        "endTime": "2024-06-10T00:00:00Z",
        "properties": {"source": "fusion-mvp", "variable": "lst_mean"},
    },
    "projects/my-gcp-project/assets/fusion/lst_2024_06_03",
)  # [U] see guide below for exact body
# or ad hoc, no asset: ee.Image.loadGeoTIFF("gs://my-bucket/fusion/lst_2024-06-03.tif")
```

Requirements for COG-backed assets: a valid COG, a bucket readable by the EE service account, and ideally the same region. Docs: [xee](https://github.com/google/Xee) · [COG-backed assets](https://developers.google.com/earth-engine/Earth_Engine_asset_from_cloud_geotiff) · [Noncommercial tiers](https://developers.google.com/earth-engine/guides/noncommercial_tiers) · [Commercial pricing](https://cloud.google.com/earth-engine/pricing) · [Satellite Embedding outside EE](https://developers.google.com/earth-engine/guides/aef_on_gcs_readme)

### 7.10 NASA Earthdata via earthaccess — IMERG, HLS, SMAP, GRACE

```python
import earthaccess
earthaccess.login(strategy="netrc")     # or EARTHDATA_TOKEN env var

res = earthaccess.search_data(short_name="GPM_3IMERGDF", version="07",
                              temporal=(START, END), bounding_box=BBOX)
files = earthaccess.open(res)            # fsspec file objects (streams over HTTPS outside us-west-2)
imerg = xr.open_mfdataset(files, engine="h5netcdf")["precipitation"]
imerg = imerg.transpose("time", "lat", "lon").sel(lon=slice(BBOX[0], BBOX[2]), lat=slice(BBOX[1], BBOX[3]))
imerg_weekly = imerg.resample(**WEEK).sum()        # mm/week (daily product is mm/day)

# HLS via CMR-STAC (works with odc-stac; needs Earthdata cookies/token for asset reads)
# https://cmr.earthdata.nasa.gov/stac/LPCLOUD  collections HLSS30_2.0, HLSL30_2.0
```

Docs: [earthaccess](https://earthaccess.readthedocs.io) · [CMR-STAC](https://cmr.earthdata.nasa.gov/stac) · [IMERG V08 transition](https://gpm.nasa.gov/data/news/update-imerg-v08-transition-schedule-aug-2026)

### 7.11 Copernicus DEM + terrain derivatives

```python
items = pystac_client.Client.open("https://earth-search.aws.element84.com/v1").search(
    collections=["cop-dem-glo-30"], bbox=BBOX).item_collection()
dem = odc.stac.load(items, bands=["data"], bbox=BBOX, crs="utm", resolution=30).data.isel(time=0)

import xrspatial
terrain = xr.Dataset({"elev": dem, "slope": xrspatial.slope(dem), "aspect": xrspatial.aspect(dem),
                      "curv": xrspatial.curvature(dem)})
# TWI / flow accumulation: pysheds or whitebox (see §5.5)
```

### 7.12 GUGiK NMT 1 m via WCS (Poland)

```python
from owslib.wcs import WebCoverageService
from pyproj import Transformer

URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModelFormatTIFF"
wcs = WebCoverageService(URL, version="2.0.1")
cov_id = list(wcs.contents)[0]  # inspect; may expose several coverages
print(wcs.contents[cov_id].boundingboxes)  # check axis labels and datum (KRON86 vs EVRF2007)

tr = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)
(e0, n0), (e1, n1) = tr.transform(BBOX[0], BBOX[1]), tr.transform(BBOX[2], BBOX[3])
# EPSG:2180 axis order in OGC services is northing, easting. Axis labels are often "x"(=N) / "y"(=E) [U]
resp = wcs.getCoverage(
    identifier=[cov_id], format="image/tiff", subsets=[("x", n0, n1), ("y", e0, e1)]
)  # swap if DescribeCoverage says otherwise
open("nmt_1m.tif", "wb").write(resp.read())
```

For larger AOIs, tile the request into chunks of a few km² [U on server limit]. Alternatively, query the **sheet-index WFS** (`…/NumerycznyModelTerenuEVRF2007/WFS/Skorowidze`) with the AOI and download the sheets from each feature's URL attribute. The [official QGIS plugin source](https://github.com/GlownyUrzadGeodezjiIKartografii/pobierzdaneGUGiK) holds the canonical layer names and URLs.

BDOT10k per class (new GeoParquet, whole country, filter by bbox):

```python
import geopandas as gpd

water = gpd.read_parquet(
    "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/GeoParquet/OT_PTWP_A.parquet",
    bbox=(e0, n0, e1, n1),
)  # bbox in the file's CRS (EPSG:2180, easting/northing order)
```

Parcels by point (ULDK):

```python
import requests

r = requests.get(
    "https://uldk.gugik.gov.pl/",
    params={
        "request": "GetParcelByXY",
        "xy": "17.03,51.11,4326",  # x,y,srid; coordinate order for 4326 [U], test both
        "result": "teryt,geom_wkt",
        "srid": "4326",
    },
    timeout=30,
)
print(r.text)  # "0\n<teryt>|SRID=4326;POLYGON(...)" ; first line 0 = OK
```

### 7.13 IMGW-PIB — current API + historical archive

```python
import requests, pandas as pd

synop = pd.DataFrame(requests.get("https://danepubliczne.imgw.pl/api/data/synop", timeout=30).json())
hydro = pd.DataFrame(
    requests.get("https://danepubliczne.imgw.pl/api/data/hydro", timeout=30).json()
)  # incl. lon/lat
# single station: /api/data/synop/station/wroclaw   (name without diacritics) or /id/{id}

# Archive (zipped CSV, cp1250, no header; column specs in *_format.txt files in each folder) [V tree, U file names]
BASE = "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/"
#   dane_meteorologiczne/{dobowe|miesieczne|terminowe}/{synop|klimat|opad}/<year>/...
#   dane_hydrologiczne/dobowe/<YYYY>/codz_<YYYY>_<MM>.zip
# Crawl the HTML directory listings, download zips, read with pd.read_csv(..., encoding="cp1250", header=None)
```

Attribution required: "Źródłem pochodzenia danych jest IMGW-PIB" (plus "dane zostały przetworzone" for processed data). For commercial use, check the HVD exemption (§4.5) [V].

### 7.14 GIOŚ air quality (Poland)

```python
BASE = "https://api.gios.gov.pl/pjp-api/v1/rest"
stations = requests.get(f"{BASE}/station/findAll", params={"size": 500}, timeout=30).json()
# v1 responses are paged and use Polish keys: inspect the JSON before parsing [U key names]
# sensors:  GET {BASE}/station/sensors/{stationId}
# data:     GET {BASE}/data/getData/{sensorId}      (recent days only)
# Rate limit: 2 req/min for most endpoints, so cache station/sensor lists.
# History: annual archives at https://powietrze.gios.gov.pl/pjp/archives (xlsx per year/pollutant)
# New "Dane 3.0" API (CC BY 4.0): https://dane.gios.gov.pl/api/powietrze/v1/...
# Noise contours (GeoJSON):       https://dane.gios.gov.pl/api/halas/v1/zasiegi-halasu
```

Swagger: [api.gios.gov.pl/pjp-api/swagger-ui](https://api.gios.gov.pl/pjp-api/swagger-ui/) · [dane.gios.gov.pl](https://dane.gios.gov.pl/)

EU-wide station data (Parquet), with the same schema for all countries:

```bash
airbase download --country PL --pollutant NO2 --pollutant PM2.5 --path data/eea   # see `airbase --help` [U flags]
```

### 7.15 GUS BDL — municipal statistics

```python
H = {"X-ClientId": os.environ.get("GUS_BDL_KEY", "")}  # optional; raises limits
B = "https://bdl.stat.gov.pl/api/v1"
vars_ = requests.get(
    f"{B}/variables/search", params={"name": "ludność", "format": "json", "page-size": 50}, headers=H
).json()
var_id = vars_["results"][0]["id"]
data = requests.get(
    f"{B}/data/by-variable/{var_id}",
    params={"unit-level": 6, "year": [2022, 2023], "format": "json", "page-size": 100},
    headers=H,
).json()
# unit-level: 0 country, 2 voivodeship, 5 powiat, 6 gmina. Join to PRG by TERYT (unit id contains TERYT digits) [U mapping]
```

Limits (anonymous / with key): 5 / 10 req/s; 100 / 500 per 15 min [V]. Docs: [BDL API](https://api.stat.gov.pl/Home/BdlApi)

### 7.16 OSM and Overture (vectors for proxies: roads, rail, buildings, POIs)

```python
import quackosm as qosm

roads = qosm.convert_geometry_to_geodataframe(
    geometry_filter=shape(AOI), tags_filter={"highway": True, "railway": True}
)  # auto-downloads PBF
```

```bash
overturemaps download --bbox=16.90,51.05,17.15,51.18 -f geoparquet --type=building -o buildings.parquet
# schema v2.0.0 since release 2026-09-23 (breaking changes in places/addresses) [V]
```

### 7.17 Writing the batch: Zarr/NetCDF DataTree + COG

```python
import rioxarray  # noqa: F401  (registers .rio)


def georef(ds, crs):
    return ds.rio.write_crs(crs).rio.set_spatial_dims(x_dim="x", y_dim="y")


batch = xr.DataTree.from_dict(
    {
        "/": xr.Dataset(
            attrs={"aoi": str(AOI), "start": START, "end": END, "cadence": "7D", "schema": "fusion-batch/0.1"}
        ),
        "/optical_s2_10m": georef(s2_weekly, s2_weekly.odc.crs),
        "/sar_s1_10m": georef(s1_weekly, s1_weekly.odc.crs),
        "/terrain_30m": georef(terrain, terrain.odc.crs),
        "/meteo_era5_0p25deg": era_weekly.rio.write_crs("EPSG:4326"),
    }
)
batch.to_zarr("batch_2024-06-03_2024-07-01.zarr", mode="w")  # Zarr v3 with zarr>=3
batch.to_netcdf("batch_2024-06-03_2024-07-01.nc", engine="h5netcdf")

# COG per variable × period (for GEE / QGIS)
for var in ["NDVI", "NDMI", "MNDWI"]:
    for t in s2_weekly.time.values:
        da = s2_weekly[var].sel(time=t).astype("float32")
        georef(da, s2_weekly.odc.crs).rio.to_raster(
            f"cog/{var}_{pd.Timestamp(t):%Y%m%d}.tif",
            driver="COG",
            compress="DEFLATE",
            blocksize=512,
            overview_resampling="average",
        )
```

---

## 8. Output specification (batch contract)

### 8.1 Zarr / NetCDF DataTree layout

```
batch_<start>_<end>.zarr/
├── .attrs: aoi (GeoJSON), start, end, cadence, schema="fusion-batch/0.1", created, request_hash
├── optical_s2_10m/        dims (time, y, x)  vars: blue…swir22, NDVI, NDWI, MNDWI, NDMI, NBR, NDBI, valid_obs
├── optical_landsat_30m/   dims (time, y, x)  vars: sr_*, lst_k, valid_obs
├── sar_s1_10m/            dims (time, y, x)  vars: vv_db, vh_db, cr_db, rvi, n_acq
├── terrain_30m/ | terrain_1m_pl/   dims (y, x)   vars: elev, slope, aspect, curv, twi
├── meteo_era5land_0p1deg/ dims (time, latitude, longitude)  vars: t2m_*, precip_mm, vpd_kpa, wind_ms, swvl1, pev
├── atmos_cams_0p1deg/     dims (time, latitude, longitude)  vars: no2, pm2p5, o3 (mean, p95)
├── atmos_s5p_l3/          dims (time, y, x)  vars: no2_trop, …, n_obs
├── stations_imgw/         dims (time, station)  vars: …; coords lon, lat, station_id
├── stations_gios/         dims (time, station)
├── population_100m/       dims (y, x)  attrs epoch, aggregation
├── landcover_10m/         dims (y, x)
└── common/  (optional)    every requested var resampled to common_crs/common_res
```

**Per-variable attributes (CF + provenance):** `standard_name` / `long_name`, `units`, `source`, `collection`, `item_ids` or `files`, `processing_baseline`, `aggregation` (e.g. `masked_median_7D`), `status` (`final` / `preliminary`), `licence`, `attribution`, `retrieved_at`.

**Chunking:** `time=1` (or full time for small AOIs), `y=x=512`. Use float32 for continuous values. Encode NaN as `_FillValue`.

**Common grid** (when `grid_policy="common"`):

| Variable type | Resampling method |
|---|---|
| Continuous, coarser source → finer grid | bilinear (or nearest to stay honest about information content) |
| Continuous, finer source → coarser grid | average |
| Categorical | mode / nearest |

Record the method in `attrs["resampling"]`. Use `odc.geo` `xr_reproject` or `rioxarray.reproject_match`.

### 8.2 COG profile (for GEE and GIS interchange)

| Setting | Value |
|---|---|
| Driver | `COG`, `COMPRESS=DEFLATE` (or ZSTD), `BLOCKSIZE=512` |
| Overviews | `OVERVIEW_RESAMPLING=average` (continuous) / `nearest` (categorical) |
| Data type | float32 with NoData = NaN, or int16 with an explicit scale/offset in tags |
| Layout | One file per *variable × period*, or a multi-band file per period with band descriptions set |
| Naming | `<group>__<var>__<YYYYMMDD>.tif` |
| Index | A STAC item per file (use `rio-stac`), so the outputs are themselves searchable |

---

## 9. Licensing and cost

### 9.1 Safe for a commercial product (attribution required)

| Source | Licence |
|---|---|
| Copernicus Sentinels, CLMS, EGMS, Copernicus DEM, CDS/ADS products | Copernicus free & open; CDS/ADS/EWDS products are **CC-BY 4.0 since 2025-07-02** [V] |
| Landsat, HLS, NASA DAAC products (IMERG, SMAP, GRACE, OPERA) | Public domain / NASA open |
| ESA WorldCover, Dynamic World, IO LULC, GHSL, WorldPop, LandScan, SoilGrids, Hansen GFC, Satellite Embedding (AlphaEarth) | CC-BY 4.0 |
| JRC GSW, CHIRPS | Unrestricted |
| GUGiK (NMT, NMPT, LiDAR, orthophoto, BDOT10k, PRG, EGiB view services) | Free for any use [V] |
| Wody Polskie, GUS BDL, GIOŚ "Dane 3.0" | CC BY 4.0 [V] |
| OSM | ODbL: share-alike applies to *derived databases* you distribute; produced works (maps, statistics) only need attribution |
| Overture | ODbL / CDLA-P-2.0 per theme |
| MS Buildings | CDLA-Permissive-2.0 |

### 9.2 Restricted — check before relying on it

| Source | Issue | Mitigation |
|---|---|---|
| **IMGW-PIB** | Commercial use of basic-network data needs an agreement, *except* High-Value Datasets [V] | Get written confirmation that the meteo observations you use are HVD |
| **Google Earth Engine** | A commercial product needs a paid plan (Basic $500/month, Pro $2,000/month) [V] | Keep GEE out of the production path, or budget for it once Dynamic World or embeddings prove essential. Clarify with Google whether prototyping counts as noncommercial [U]. |
| E-OBS, CEMS-FLOODS (GloFAS/EFAS), EDO | Own licences [U] | Read the terms; ERA5-Land and E-OBS are partly substitutable |
| TESSERA, Major TOM (?), MERIT Hydro (ODbL / NC) | Share-alike or non-commercial | Prefer AlphaEarth (CC-BY), HydroSHEDS v1 |
| **FABDEM, MSWEP, WAQI** | Non-commercial | Do not use in the product |
| EGMS, GDOŚ, GIOŚ noise API | Licence not stated in the metadata [U] | Ask the data owner; attribute regardless |

### 9.3 Small spends that remove real pain

| Spend | Approx. cost | What it buys |
|---|---|---|
| **Compute in the data's region** (AWS us-west-2 / eu-central-1, Azure West Europe, CloudFerro WAW) | VM €30–150/month | 5–20× faster COG reads; no egress; direct S3 for NASA Earthdata (us-west-2 only) |
| **AWS requester-pays** (Landsat, S1 GRD on Earth Search) | ~$0.0004 per 1k GETs + egress ~$0.09/GB outside AWS; ~free in-region | Avoids the PC token dance for big Landsat pulls |
| **Object storage for the Zarr cache** (Hetzner / Backblaze / CloudFerro) | €5–20/month per TB | Persistent cache; serves batches to the team |
| **GEE Basic** | $500/month [V] | Commercial right to use Dynamic World, Cloud Score+, Satellite Embedding, and server-side reductions |
| **Open-Meteo Professional** | ~$99/month (2023 price; current price behind checkout) [U] | Commercial historical-weather API (ERA5/ERA5-Land + national models) for **point time series without queues**. Convenient for station-like features. |
| **Sentinel Hub commercial (Planet Insights Platform)** | Plan-based [U] | Higher PU quotas beyond the CDSE free tier for the Statistical/Process API |
| **CDSE quota above the free tier** | Via CloudFerro/CREODIAS [U] | Raises the 4 × 20 MB/s S3 cap |

---

## 10. Gotchas checklist (read before writing adapters)

1. **Sentinel-2 offset.** From baseline 04.00 (2022-01-25) on, reflectance = (DN − 1000)/10000. Read the offset from item metadata; never hard-code by date across collections. CDSE products now carry baseline N0513-style IDs [V].
2. **Duplicate S2 scenes** from overlapping MGRS tiles: always use `groupby="solar_day"` in `odc-stac`.
3. **Median needs a single time chunk**: call `.chunk(time=-1)` before `.median()`. Watch memory with large AOIs; process in spatial tiles.
4. **SAR aggregation in linear units**, never in dB. Keep a single orbit direction per series.
5. **ERA5-Land accumulations** are cumulative from 00 UTC; ERA5 hourly accumulations are per hour. Precipitation is in metres, temperature in kelvin.
6. **ARCO-ERA5 longitudes run 0–360 and latitude is descending.**
7. **EPSG:2180 axis order** in WFS 1.1/2.0 and WCS is *northing, easting*. Polish axis labels "x/y" mean N/E. Test with a known point.
8. **Height datums:** PL-KRON86-NH vs PL-EVRF2007-NH (GUGiK) vs EGM2008 (Copernicus DEM). Don't mix them.
9. **Planetary Computer SAS tokens** expire after about 1 h. Re-sign for long jobs.
10. **Requester-pays buckets** need `AWS_REQUEST_PAYER=requester` plus your credentials.
11. **CDSE:** the OpenSearch catalogue was switched off on 2026-03-02 and the old `catalogue.dataspace.copernicus.eu/stac` was deprecated on 2025-11-17 [V]. **`sentinelsat` / SciHub are dead**, so don't use them. S2 assets on CDSE are JP2 (slower than COG); S3 is limited to 4 connections.
12. **Landsat 7 SLC-off** (after 2003-05) causes striping; L7 is decommissioned. Landsat 5 ends in 2013 and there is a gap before L8 (2013-04), filled only by L7.
13. **Sentinel-1 gap:** only S1A was flying from 2022-01 to 2025, so revisit was 12 days with fewer acquisitions. Expect irregular density across years.
14. **MODIS is ending** (late 2026 to early 2027). Build on VIIRS, Sentinel-3 or Landsat instead.
15. **IMERG V07 → V08** transition from fall 2026. Pin the version in your cache key.
16. **CAMS forecasts** are kept for only 3 years. Archive them yourself if you need them [V].
17. **Station timestamps:** IMGW synop uses UTC; GIOŚ may use local time [U]. Normalise to UTC.
18. **GIOŚ rate limit** is 2 requests/min for station and sensor listings. Cache aggressively [V].
19. **xee 0.1.x** has a breaking API change: use `helpers.fit_geometry` / `extract_grid_params` [V].
20. **Overture schema v2** (2026-09-23) has breaking changes in `places` and `addresses` [V].
21. **Licences propagate:** record `licence` per variable so the batch can be filtered to "commercial-safe" automatically.

---

## 11. Open decisions for the team

| # | Question | Default if undecided |
|---|---|---|
| 1 | Typical AOI size: gmina (~100 km²), catchment (~1,000 km²), or voivodeship? This drives local reads vs server-side aggregation. | ≤1,000 km² local; larger goes to openEO/Statistical API |
| 2 | Store native pixels per batch, or only weekly composites? | Weekly composites + STAC references to source items (re-fetchable) |
| 3 | Is the main consumer ML (embedding + tabular features) or GIS/visual analysis? | Both. Provide Zarr for ML and COG for GIS. |
| 4 | Is GEE in the product path or only for R&D? This is a licence decision (§9.2). | R&D only until a commercial plan is approved |
| 5 | IMGW commercial terms: confirm the HVD scope in writing | Use ERA5/ERA5-Land as the commercial-safe baseline; IMGW as validation |
| 6 | Noise: regulatory maps only (2022 round) or a modelled proxy (NoiseModelling + traffic)? | Regulatory maps + distance/traffic proxies |
| 7 | Latency requirement: "last week" (preliminary products) or "final" only? | Both, with `status` flag and automatic recompute |
| 8 | Storage budget for cache (TB) | 1 TB object storage |

---

## 12. Link index (documentation for coding agents)

### Catalogues and platforms
- STAC spec: https://stacspec.org · STAC Browser: https://radiantearth.github.io/stac-browser/
- Earth Search: https://github.com/Element84/earth-search · API: https://earth-search.aws.element84.com/v1
- Planetary Computer: https://planetarycomputer.microsoft.com/docs · API: https://planetarycomputer.microsoft.com/api/stac/v1 · Catalog: https://planetarycomputer.microsoft.com/catalog · PC Pro: https://learn.microsoft.com/en-us/azure/planetary-computer/
- CDSE docs: https://documentation.dataspace.copernicus.eu/ · STAC: https://stac.dataspace.copernicus.eu/v1/ · STAC browser: https://browser.stac.dataspace.copernicus.eu · OData: https://documentation.dataspace.copernicus.eu/APIs/OData.html · S3: https://documentation.dataspace.copernicus.eu/APIs/S3.html · Sentinel Hub: https://documentation.dataspace.copernicus.eu/APIs/SentinelHub.html · openEO: https://documentation.dataspace.copernicus.eu/APIs/openEO/openEO.html · Quotas: https://documentation.dataspace.copernicus.eu/Quotas.html · S3 keys: https://eodata-s3keysmanager.dataspace.copernicus.eu/ · JupyterHub: https://jupyterhub.dataspace.copernicus.eu
- Google Earth Engine: https://developers.google.com/earth-engine · Catalog: https://developers.google.com/earth-engine/datasets · Community catalog: https://gee-community-catalog.org · Noncommercial tiers: https://developers.google.com/earth-engine/guides/noncommercial_tiers · Pricing: https://cloud.google.com/earth-engine/pricing · COG assets: https://developers.google.com/earth-engine/Earth_Engine_asset_from_cloud_geotiff
- NASA Earthdata: https://www.earthdata.nasa.gov · earthaccess: https://earthaccess.readthedocs.io · CMR-STAC: https://cmr.earthdata.nasa.gov/stac · Earthdata Login: https://urs.earthdata.nasa.gov
- ASF: https://docs.asf.alaska.edu/asf_search/basics/ · HyP3: https://hyp3-docs.asf.alaska.edu · NISAR: https://nisar-docs.asf.alaska.edu
- USGS Landsat: https://www.usgs.gov/landsat-missions · LandsatLook STAC: https://landsatlook.usgs.gov/stac-server · M2M: https://m2m.cr.usgs.gov/ · AWS: https://registry.opendata.aws/usgs-landsat/
- ECMWF CDS: https://cds.climate.copernicus.eu/how-to-api · ADS: https://ads.atmosphere.copernicus.eu/how-to-api · EWDS: https://ewds.climate.copernicus.eu · ARCO-ERA5: https://github.com/google-research/arco-era5 · Earthmover ERA5: https://docs.earthmover.io/sample-data/era5 · NCAR ERA5 on AWS: https://registry.opendata.aws/nsf-ncar-era5/
- Source Cooperative: https://source.coop · AWS Open Data registry: https://registry.opendata.aws

### Missions and products
- Sentinel-2 processing: https://sentiwiki.copernicus.eu/web/s2-processing · Baselines: https://documentation.dataspace.copernicus.eu/Data/Others/Sentinel2_L2A_baseline.html
- Sentinel-1: https://sentiwiki.copernicus.eu/web/s1-mission · Sentinel-5P on CDSE: https://documentation.dataspace.copernicus.eu/Data/SentinelMissions/Sentinel5P.html
- Landsat C2 L2: https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products · Landsat Next/10: https://www.usgs.gov/landsat-missions/landsat-next
- HLS: https://hls.gsfc.nasa.gov · OPERA: https://www.jpl.nasa.gov/go/opera/products/
- MODIS → VIIRS: https://www.earthdata.nasa.gov/data/alerts-outages/transition-from-modis-viirs
- IMERG: https://gpm.nasa.gov/data/imerg · CHIRPS: https://www.chc.ucsb.edu/data · OPERA radar: https://eumetnet.github.io/openradardata-documentation/
- EGMS: https://land.copernicus.eu/en/products/european-ground-motion-service · Explorer: https://egms.land.copernicus.eu
- CLMS: https://land.copernicus.eu · CLMS API: https://eea.github.io/clms-api-docs/download.html · CLMS on CDSE: https://documentation.dataspace.copernicus.eu/Data/CopernicusServices/CLMS.html
- EDO/GDO drought: https://drought.emergency.copernicus.eu
- ESA WorldCover: https://esa-worldcover.org · Dynamic World: https://dynamicworld.app · JRC GSW: https://global-surface-water.appspot.com · Hansen GFC: https://glad.earthengine.app/view/global-forest-change
- SoilGrids: https://www.isric.org/explore/soilgrids · WCS: https://maps.isric.org
- HydroSHEDS: https://www.hydrosheds.org · EU-Hydro: https://land.copernicus.eu/en/products/eu-hydro
- GHSL: https://human-settlement.emergency.copernicus.eu · WorldPop API: https://www.worldpop.org/sdi/introapi/ · Eurostat grids: https://ec.europa.eu/eurostat/en/web/gisco/geodata/population-distribution/population-grids · LandScan: https://landscan.ornl.gov
- EEA AQ download: https://eeadmz1-downloads-webapp.azurewebsites.net/ · airbase: https://airbase.readthedocs.io · OpenAQ: https://docs.openaq.org
- EEA noise: https://www.eea.europa.eu/en/topics/in-depth/noise · NoiseModelling: https://noise-planet.org/noisemodelling.html
- Satellite Embedding: https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_SATELLITE_EMBEDDING_V1_ANNUAL · GCS readme: https://developers.google.com/earth-engine/guides/aef_on_gcs_readme · Source Coop mirror: https://source.coop/tge-labs/aef · TESSERA: https://github.com/ucam-eo/tessera
- Overture: https://docs.overturemaps.org · OSM Geofabrik: https://download.geofabrik.de/europe/poland.html · Overpass: https://wiki.openstreetmap.org/wiki/Overpass_API · MS Buildings: https://github.com/microsoft/GlobalMLBuildingFootprints

### Poland
- Geoportal data: https://www.geoportal.gov.pl/pl/dane/ · NMT: https://www.geoportal.gov.pl/pl/dane/numeryczny-model-terenu-nmt/ · Download UI: https://mapy.geoportal.gov.pl/imapnext/imap/index.html?moduleId=modulPD
- GUGiK QGIS plugin (endpoint reference): https://github.com/GlownyUrzadGeodezjiIKartografii/pobierzdaneGUGiK
- OpenData GUGiK (PRG, BDOT10k): https://opendata.geoportal.gov.pl/ · OGC API Features: https://ogcapi.geoportal.gov.pl/res/ims/maps/PRG/apiFeatures
- ULDK: https://uldk.gugik.gov.pl/opis.html
- IMGW: https://danepubliczne.imgw.pl/pl/apiinfo · Archive: https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/
- GIOŚ: https://api.gios.gov.pl/pjp-api/swagger-ui/ · Dane 3.0: https://dane.gios.gov.pl/ · Archives: https://powietrze.gios.gov.pl/pjp/archives
- GUS: https://api.stat.gov.pl/Home/BdlApi · Geostatistics: https://geo.stat.gov.pl · DBW: https://api-dbw.stat.gov.pl/apidocs/ · SDP: https://api-sdp.stat.gov.pl/apidocs/
- PIG-PIB: https://gis.pgi.gov.pl · CBDG: https://cbdg.pgi.gov.pl
- Wody Polskie hydroportal: https://wody.isok.gov.pl/hydroportal.html
- GDOŚ: https://geoserwis.gdos.gov.pl · WMS/WFS: https://sdi.gdos.gov.pl/wms, https://sdi.gdos.gov.pl/wfs
- ARiMR geoportal: https://geoportal.arimr.gov.pl
- GDDKiA GPR 2025: https://www.gov.pl/web/gddkia/generalny-pomiar-ruchu-2025
- dane.gov.pl API: https://api.dane.gov.pl/1.4/ · Docs: https://api.dane.gov.pl/doc

### Python libraries
- pystac-client https://pystac-client.readthedocs.io · pystac https://pystac.readthedocs.io · odc-stac https://odc-stac.readthedocs.io · odc-geo https://odc-geo.readthedocs.io · stackstac https://stackstac.readthedocs.io
- xarray https://docs.xarray.dev (DataTree: https://docs.xarray.dev/en/stable/user-guide/hierarchical-data.html) · rioxarray https://corteva.github.io/rioxarray · zarr https://zarr.readthedocs.io · icechunk https://icechunk.io · dask https://docs.dask.org
- rasterio https://rasterio.readthedocs.io · GDAL https://gdal.org (COG driver: https://gdal.org/en/stable/drivers/raster/cog.html) · rio-stac https://github.com/developmentseed/rio-stac
- spyndex https://spyndex.readthedocs.io · Awesome Spectral Indices https://awesome-ee-spectral-indices.readthedocs.io · xclim https://xclim.readthedocs.io · xarray-spatial https://xarray-spatial.readthedocs.io · pysheds https://github.com/mdbartos/pysheds · whitebox https://www.whiteboxgeo.com/manual/wbt_book/
- planetary-computer https://github.com/microsoft/planetary-computer-sdk-for-python · sentinelhub-py https://sentinelhub-py.readthedocs.io · openeo https://open-eo.github.io/openeo-python-client/ · earthaccess https://earthaccess.readthedocs.io · cdsapi https://github.com/ecmwf/cdsapi
- earthengine-api https://developers.google.com/earth-engine/guides/python_install · xee https://github.com/google/Xee · geemap https://geemap.org · eemont https://eemont.readthedocs.io
- owslib https://owslib.readthedocs.io · geopandas https://geopandas.org · quackosm https://kraina-ai.github.io/quackosm/ · osmnx https://osmnx.readthedocs.io · overturemaps-py https://github.com/OvertureMaps/overturemaps-py · PDAL https://pdal.io
- cubo (STAC cube helper) https://github.com/ESDS-Leipzig/cubo · stac-geoparquet https://github.com/stac-utils/stac-geoparquet

---

*Prepared 2026-10-06. Items marked [U] need confirmation during implementation. Re-verify quotas and pricing each quarter.*
