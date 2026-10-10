# geomcp tool examples (contract reference)

Generated from real tool calls by `scripts/make_tool_examples.py`; do not edit by hand.
Long lists and geometries are shortened (`... N more`). Values are real data for Wrocław and nearby gminas.

Conventions:
- Every tool returns `structuredContent` plus a short text summary.
- Every value carries units, status (`final`, `preliminary`, `missing`), licence and attribution.
- Expected failures return `isError` with `{error: {code, message, hint}}`.
- Calls that would exceed the sync budget (45 s) return a `job_id`; `get_job` long-polls for up to 20 s.

## resolve_area

### A gmina by name

Call:

```json
{
  "tool": "resolve_area",
  "arguments": {
    "query": "gmina Kąty Wrocławskie"
  }
}
```

Structured content:

```json
{
  "selected": "a_osm_R3048177",
  "candidates": [
    {
      "area_id": "a_osm_R3048177",
      "name": "gmina Kąty Wrocławskie",
      "kind": "gmina",
      "area_km2": 176.717125,
      "bbox": [
        16.682618,
        50.954185,
        16.963377,
        51.112636
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": "... (simplified to at most ~200 vertices)"
      },
      "admin": {
        "display_name": "gmina Kąty Wrocławskie, powiat wrocławski, województwo dolnośląskie, Polska",
        "terc": "0223043"
      },
      "source": "OpenStreetMap via Nominatim",
      "licence": "ODbL-1.0 (© OpenStreetMap contributors)"
    }
  ]
}
```

Text:

```
Resolved 'gmina Kąty Wrocławskie' to gmina Kąty Wrocławskie (gmina, TERC 0223043), 176.7 km² (area_id a_osm_R3048177).
```

### Coordinates with a square buffer (the golden AOI)

Call:

```json
{
  "tool": "resolve_area",
  "arguments": {
    "query": "51.110,17.032",
    "buffer_m": 1581,
    "shape": "square"
  }
}
```

Structured content:

```json
{
  "selected": "a_pt_51.1100_17.0320_sq1581",
  "candidates": [
    {
      "area_id": "a_pt_51.1100_17.0320_sq1581",
      "name": "3.16 km square around 51.1100, 17.0320",
      "kind": "buffer",
      "area_km2": 9.998244,
      "bbox": [
        17.009418,
        51.095787,
        17.054582,
        51.124209
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": "... (simplified to at most ~200 vertices)"
      },
      "admin": {},
      "source": "user coordinates",
      "licence": "n/a"
    }
  ]
}
```

Text:

```
Resolved '51.110,17.032' to 3.16 km square around 51.1100, 17.0320 (buffer), 10.0 km² (area_id a_pt_51.1100_17.0320_sq1581).
```

### An ambiguous name

Call:

```json
{
  "tool": "resolve_area",
  "arguments": {
    "query": "Lipno"
  }
}
```

Structured content:

```json
{
  "selected": null,
  "candidates": [
    {
      "area_id": "a_osm_R439294",
      "name": "Lipno",
      "kind": "place",
      "area_km2": 18.738248,
      "bbox": [
        13.621957,
        50.27344,
        13.738328,
        50.327933
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": "... (simplified to at most ~200 vertices)"
      },
      "admin": {
        "display_name": "Lipno, okres Louny, Ústecký kraj, Česko"
      },
      "source": "OpenStreetMap via Nominatim",
      "licence": "ODbL-1.0 (© OpenStreetMap contributors)"
    },
    {
      "area_id": "a_osm_R2643776",
      "name": "Lipno",
      "kind": "gmina",
      "area_km2": 10.99526,
      "bbox": [
        19.131251,
        52.827827,
        19.219735,
        52.857142
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": "... (simplified to at most ~200 vertices)"
      },
      "admin": {
        "display_name": "Lipno, powiat lipnowski, województwo kujawsko-pomorskie, 87-600, Polska",
        "terc": "0408011"
      },
      "source": "OpenStreetMap via Nominatim",
      "licence": "ODbL-1.0 (© OpenStreetMap contributors)"
    },
    {
      "area_id": "a_osm_R2643780",
      "name": "Lipno",
      "kind": "place",
      "area_km2": 10.99526,
      "bbox": [
        19.131251,
        52.827827,
        19.219735,
        52.857142
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": "... (simplified to at most ~200 vertices)"
      },
      "admin": {
        "display_name": "Lipno, powiat lipnowski, województwo kujawsko-pomorskie, 87-600, Polska"
      },
      "source": "OpenStreetMap via Nominatim",
      "licence": "ODbL-1.0 (© OpenStreetMap contributors)"
    },
    {
      "area_id": "a_pt_53.7004_16.0933_ci1000",
      "name": "Circle of radius 1.00 km around Lipno, gmina Połczyn-Zdrój, powiat świdwiński, województwo zachodniopomorskie, 78-326, Polska",
      "kind": "buffer",
      "area_km2": 3.136548,
      "bbox": [
        16.078159,
        53.691415,
        16.108441,
        53.709385
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": "... (simplified to at most ~200 vertices)"
      },
      "admin": {},
      "source": "user coordinates",
      "licence": "n/a"
    },
    "... 1 more"
  ]
}
```

Text:

```
'Lipno' is ambiguous; ask the user which one: a_osm_R439294: Lipno, okres Louny, Ústecký kraj, Česko; a_osm_R2643776: Lipno, powiat lipnowski, województwo kujawsko-pomorskie, 87-600, Polska; a_osm_R2643780: Lipno, powiat lipnowski, województwo kujawsko-pomorskie, 87-600, Polska; a_pt_53.7004_16.0933_ci1000: Circle of radius 1.00 km around Lipno, gmina Połczyn-Zdrój, powiat świdwiński, województwo zachodniopomorskie, 78-326, Polska; a_pt_54.5627_17.2779_ci1000: Circle of radius 1.00 km around Lipno, gmina Główczyce, powiat słupski, województwo pomorskie, 76-220, Polska
```

### A Polish cadastral parcel

Call:

```json
{
  "tool": "resolve_area",
  "arguments": {
    "query": "026401_1.0001.2395"
  }
}
```

Structured content:

```json
{
  "selected": "a_uldk_026401_1.0001.2395",
  "candidates": [
    {
      "area_id": "a_uldk_026401_1.0001.2395",
      "name": "Parcel 026401_1.0001.2395",
      "kind": "parcel",
      "area_km2": 0.000618,
      "bbox": [
        17.031547,
        51.109965,
        17.032118,
        51.11024
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": "... (simplified to at most ~200 vertices)"
      },
      "admin": {},
      "source": "GUGiK ULDK",
      "licence": "GUGiK open data"
    }
  ]
}
```

Text:

```
Resolved '026401_1.0001.2395' to Parcel 026401_1.0001.2395 (parcel), 618 m² (area_id a_uldk_026401_1.0001.2395).
```

### Another gmina

Call:

```json
{
  "tool": "resolve_area",
  "arguments": {
    "query": "gmina Kobierzyce"
  }
}
```

Structured content:

```json
{
  "selected": "a_osm_R3049635",
  "candidates": [
    {
      "area_id": "a_osm_R3049635",
      "name": "gmina Kobierzyce",
      "kind": "gmina",
      "area_km2": 149.20352,
      "bbox": [
        16.838796,
        50.863868,
        17.034889,
        51.057735
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": "... (simplified to at most ~200 vertices)"
      },
      "admin": {
        "display_name": "gmina Kobierzyce, powiat wrocławski, województwo dolnośląskie, Polska",
        "terc": "0223052"
      },
      "source": "OpenStreetMap via Nominatim",
      "licence": "ODbL-1.0 (© OpenStreetMap contributors)"
    }
  ]
}
```

Text:

```
Resolved 'gmina Kobierzyce' to gmina Kobierzyce (gmina, TERC 0223052), 149.2 km² (area_id a_osm_R3049635).
```

## list_data

### Weather variables

Call:

```json
{
  "tool": "list_data",
  "arguments": {
    "topic": "weather"
  }
}
```

Structured content:

```json
{
  "sources": [
    {
      "source": {
        "id": "era5_land",
        "name": "ERA5-Land hourly time series",
        "variables": [
          {
            "name": "t2m",
            "units": "degC",
            "topic": "weather",
            "description": "Air temperature at 2 m, daily mean",
            "static": false,
            "aggregation": "mean"
          },
          {
            "name": "t2m_max",
            "units": "degC",
            "topic": "weather",
            "description": "Daily maximum air temperature",
            "static": false,
            "aggregation": "mean"
          },
          {
            "name": "t2m_min",
            "units": "degC",
            "topic": "weather",
            "description": "Daily minimum air temperature",
            "static": false,
            "aggregation": "mean"
          },
          {
            "name": "precipitation",
            "units": "mm",
            "topic": "weather",
            "description": "Total precipitation",
            "static": false,
            "aggregation": "sum"
          },
          "... 10 more"
        ],
        "access_pattern": "timeseries",
        "resolution": "0.1 deg (~9 km)",
        "coverage": [
          "1950-01-01",
          null
        ],
        "latency": "P6D",
        "licence": "CC-BY-4.0",
        "attribution": "Muñoz Sabater, J. (2019): ERA5-Land hourly data from 1950 to present. Copernicus Climate Change Service (C3S) Climate Data Store (CDS)",
        "commercial_safe": true,
        "auth_env": [
          "GEOMCP_CDS_KEY"
        ]
      },
      "health": "ok",
      "hint": ""
    }
  ],
  "sentinel_hub_quota": {
    "month": "2026-10",
    "units_used": 1325.9,
    "units_limit": 10000.0,
    "requests_used": 228,
    "requests_limit": 10000
  }
}
```

Text:

```
1 source(s), 14 variable(s) for topic 'weather'.
- ERA5-Land hourly time series: t2m [degC], t2m_max [degC], t2m_min [degC], precipitation [mm], wind_speed [m s-1], solar_radiation [MJ m-2], soil_moisture [m3 m-3], vpd [kPa], hot_days [days], summer_days [days], tropical_nights [days], frost_days [days], heavy_rain_days [days], dry_days [days]
Sentinel Hub this month: 1,326 of 10,000 processing units.
```

## get_timeseries

### Ten years, yearly, three variables

Call:

```json
{
  "tool": "get_timeseries",
  "arguments": {
    "area_id": "a_osm_R3048177",
    "variables": [
      "ndvi",
      "t2m",
      "precipitation"
    ],
    "start": "2016-01-01",
    "end": "2026-01-01",
    "cadence": "P1Y"
  }
}
```

Structured content:

```json
{
  "area_id": "a_osm_R3048177",
  "period": {
    "start": "2016-01-01",
    "end": "2026-01-01",
    "cadence": "P1Y",
    "n_periods": 10
  },
  "series": [
    {
      "variable": "ndvi",
      "units": "1",
      "summary": {
        "trend_per_year": 0.000624,
        "trend_method": "Theil-Sen",
        "trend_p_value": 0.7205,
        "min": {
          "value": 0.489083,
          "period": "2018-01-01"
        },
        "max": {
          "value": 0.558023,
          "period": "2017-01-01"
        },
        "latest": {
          "value": 0.516424,
          "period": "2025-01-01"
        },
        "gaps": 0
      },
      "provenance": {
        "source": "Sentinel-2 L2A",
        "backend": "sentinel-hub-statistical",
        "collection": "sentinel-2-l2a",
        "method": "per acquisition: mean over cloud-free pixels (SCL classes 4-6) at 150 m (bilinear); acquisitions with under 20% cloud-free area dropped; period value = mean of acquisition means",
        "licence": "Copernicus open data (CC-BY-4.0)",
        "attribution": "Contains modified Copernicus Sentinel data",
        "commercial_safe": true,
        "retrieved_at": "2026-10-10T21:57:54.818345Z"
      }
    },
    {
      "variable": "t2m",
      "units": "degC",
      "summary": {
        "trend_per_year": 0.100529,
        "trend_method": "Theil-Sen",
        "trend_p_value": 0.5915,
        "min": {
          "value": 9.300854,
          "period": "2021-01-01"
        },
        "max": {
          "value": 11.716753,
          "period": "2024-01-01"
        },
        "latest": {
          "value": 10.197564,
          "period": "2025-01-01"
        },
        "gaps": 0
      },
      "provenance": {
        "source": "ERA5-Land hourly time series",
        "backend": "cds-timeseries",
        "collection": "reanalysis-era5-land-timeseries",
        "method": "grid cell 51.0N 16.8E nearest to the area centroid; hourly values reduced to daily, then mean of daily values per period; the area is larger than one grid cell, so this is the cell at its centroid",
        "licence": "CC-BY-4.0",
        "attribution": "Muñoz Sabater, J. (2019): ERA5-Land hourly data from 1950 to present. Copernicus Climate Change Service (C3S) Climate Data Store (CDS)",
        "commercial_safe": true,
        "retrieved_at": "2026-10-10T21:57:54.820347Z"
      }
    },
    {
      "variable": "precipitation",
      "units": "mm",
      "summary": {
        "trend_per_year": 3.64035,
        "trend_method": "Theil-Sen",
        "trend_p_value": 0.7205,
        "min": {
          "value": 607.89121,
          "period": "2018-01-01"
        },
        "max": {
          "value": 939.18177,
          "period": "2023-01-01"
        },
        "latest": {
          "value": 672.92387,
          "period": "2025-01-01"
        },
        "gaps": 0
      },
      "provenance": {
        "source": "ERA5-Land hourly time series",
        "backend": "cds-timeseries",
        "collection": "reanalysis-era5-land-timeseries",
        "method": "grid cell 51.0N 16.8E nearest to the area centroid; hourly values reduced to daily, then sum of daily totals per period; the area is larger than one grid cell, so this is the cell at its centroid",
        "licence": "CC-BY-4.0",
        "attribution": "Muñoz Sabater, J. (2019): ERA5-Land hourly data from 1950 to present. Copernicus Climate Change Service (C3S) Climate Data Store (CDS)",
        "commercial_safe": true,
        "retrieved_at": "2026-10-10T21:57:54.825889Z"
      }
    }
  ],
  "rows_columns": [
    "period",
    "variable",
    "mean",
    "p10",
    "... 4 more"
  ],
  "rows": [
    [
      "2016-01-01",
      "ndvi",
      0.513886,
      0.255162,
      "... 4 more"
    ],
    [
      "2016-01-01",
      "t2m",
      9.909489,
      -0.158735,
      "... 4 more"
    ],
    [
      "2016-01-01",
      "precipitation",
      833.36205,
      null,
      "... 4 more"
    ],
    [
      "2017-01-01",
      "ndvi",
      0.558023,
      0.260707,
      "... 4 more"
    ],
    "... 26 more"
  ],
  "rows_truncated": null,
  "gaps": [],
  "notices": [],
  "cost": {
    "duration_s": 0.04,
    "sentinel_hub_units": 0.0,
    "cache_hits": 30,
    "cache_misses": 0
  },
  "downloads": {
    "csv": "~/.geomcp/files/timeseries-a_osm_r3048177-b3b853eda0b222fdf1660d3c062e0010.csv",
    "parquet": "~/.geomcp/files/timeseries-a_osm_r3048177-b3b853eda0b222fdf1660d3c062e0010.parquet"
  }
}
```

Text:

```
10 periods (P1Y) from 2016-01-01 to 2026-01-01.
ndvi: seasonal trend +0.000624/yr (p=0.72); latest 0.516 (2025); 0 gap(s); source Sentinel-2 L2A. Method: per acquisition: mean over cloud-free pixels (SCL classes 4-6) at 150 m (bilinear); acquisitions with under 20% cloud-free area dropped; period value = mean of acquisition means.
t2m: seasonal trend +0.101 degC/yr (p=0.59); latest 10.2 degC (2025); 0 gap(s); source ERA5-Land hourly time series. Method: grid cell 51.0N 16.8E nearest to the area centroid; hourly values reduced to daily, then mean of daily values per period; the area is larger than one grid cell, so this is the cell at its centroid.
precipitation: seasonal trend +3.64 mm/yr (p=0.72); latest 673 mm (2025); 0 gap(s); source ERA5-Land hourly time series. Method: grid cell 51.0N 16.8E nearest to the area centroid; hourly values reduced to daily, then sum of daily totals per period; the area is larger than one grid cell, so this is the cell at its centroid.
Full table: ~/.geomcp/files/timeseries-a_osm_r3048177-b3b853eda0b222fdf1660d3c062e0010.csv
```

## get_summary

### Terrain, land, infrastructure, people, recent conditions

Call:

```json
{
  "tool": "get_summary",
  "arguments": {
    "area_id": "a_osm_R3048177",
    "variables": [
      "elevation_ground",
      "slope_ground",
      "landcover",
      "buildings",
      "... 9 more"
    ]
  }
}
```

Structured content:

```json
{
  "area": {
    "area_id": "a_osm_R3048177",
    "name": "gmina Kąty Wrocławskie",
    "kind": "gmina",
    "area_km2": 176.717125,
    "bbox": [
      16.682618,
      50.954185,
      16.963377,
      51.112636
    ],
    "geometry": {
      "type": "Polygon",
      "coordinates": "... (simplified to at most ~200 vertices)"
    },
    "admin": {
      "display_name": "gmina Kąty Wrocławskie, powiat wrocławski, województwo dolnośląskie, Polska",
      "terc": "0223043"
    },
    "source": "OpenStreetMap via Nominatim",
    "licence": "ODbL-1.0 (© OpenStreetMap contributors)"
  },
  "facts": [
    {
      "variable": "elevation_ground",
      "units": "m",
      "value": {
        "min": 119.28,
        "p10": 125.96,
        "mean": 138.69,
        "p90": 153.19,
        "max": 181.88
      },
      "period": null,
      "status": "final",
      "provenance": {
        "source": "GUGiK NMT (digital terrain model, Poland)",
        "backend": "gugik-wcs",
        "collection": "DTM_PL-KRON86-NH",
        "method": "bare-ground model at 19 m cells inside the area; heights in PL-KRON86-NH (differ from EGM2008 by a few decimetres; do not mix with Copernicus DEM)",
        "licence": "GUGiK open data (free for any use)",
        "attribution": "Główny Urząd Geodezji i Kartografii (GUGiK), NMT",
        "commercial_safe": true,
        "retrieved_at": "2026-10-10T21:57:55.195714Z"
      }
    },
    {
      "variable": "slope_ground",
      "units": "degree",
      "value": {
        "mean": 1.09,
        "p90": 1.98,
        "p99": 8.77
      },
      "period": null,
      "status": "final",
      "provenance": {
        "source": "GUGiK NMT (digital terrain model, Poland)",
        "backend": "gugik-wcs",
        "collection": "DTM_PL-KRON86-NH",
        "method": "bare-ground model at 19 m cells inside the area; heights in PL-KRON86-NH (differ from EGM2008 by a few decimetres; do not mix with Copernicus DEM)",
        "licence": "GUGiK open data (free for any use)",
        "attribution": "Główny Urząd Geodezji i Kartografii (GUGiK), NMT",
        "commercial_safe": true,
        "retrieved_at": "2026-10-10T21:57:55.195714Z"
      }
    },
    {
      "variable": "landcover",
      "units": "fraction",
      "value": {
        "cropland": 0.6706,
        "tree_cover": 0.1608,
        "grassland": 0.0973,
        "built_up": 0.063,
        "bare_sparse_vegetation": 0.0054,
        "permanent_water": 0.0023,
        "herbaceous_wetland": 0.0007
      },
      "period": "2021",
      "status": "final",
      "provenance": {
        "source": "ESA WorldCover 2021 v200",
        "backend": "cloud-optimised GeoTIFF (AWS Open Data)",
        "collection": "esa-worldcover v200 (2021)",
        "method": "share of 10 m pixels inside the area polygon per class",
        "licence": "CC-BY-4.0",
        "attribution": "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium",
        "commercial_safe": true,
        "retrieved_at": "2026-10-10T21:57:55.217315Z"
      }
    },
    {
      "variable": "buildings",
      "units": "count",
      "value": 17474.0,
      "period": "2026-10-09",
      "status": "final",
      "provenance": {
        "source": "OpenStreetMap (Overpass API)",
        "backend": "overpass",
        "collection": "planet",
        "method": "objects inside the area polygon as of 2026-10-09; completeness depends on volunteer mapping",
        "licence": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors",
        "commercial_safe": true,
        "retrieved_at": "2026-10-10T21:57:55.217425Z"
      }
    },
    "... 9 more"
  ],
  "gaps": [],
  "notices": [],
  "cost": {
    "duration_s": 0.4,
    "sentinel_hub_units": 0.0,
    "cache_hits": 12,
    "cache_misses": 0
  }
}
```

Text:

```
gmina Kąty Wrocławskie (176.7 km²):
elevation_ground: min 119, p10 126, mean 139, p90 153, max 182 m (final; GUGiK NMT (digital terrain model, Poland)).
slope_ground: mean 1.09, p90 1.98, p99 8.77 degree (final; GUGiK NMT (digital terrain model, Poland)).
landcover as of 2021: cropland 67%, tree cover 16%, grassland 10%, built up 6%, bare sparse vegetation 1% (final; ESA WorldCover 2021 v200).
buildings as of 2026-10-09: 17,474 (final; OpenStreetMap (Overpass API)).
road_length as of 2026-10-09: 566 km (final; OpenStreetMap (Overpass API)).
schools as of 2026-10-09: 21 (final; OpenStreetMap (Overpass API)).
population over 2020/2025: latest 31,145, 2020 28,554 persons (final; GUS Local Data Bank (BDL)).
budget_income_per_capita over 2020/2025: latest 11,128, 2020 6,880 PLN (final; GUS Local Data Bank (BDL)).
pm10_station over 2026-10-09T21:00Z/2026-10-10T20:00Z: last 24h mean 9.4, latest hour 12.6, hours 24 ug m-3 (preliminary; GIOŚ air-quality monitoring stations).
ndvi over 2026-08-11/2026-10-10: mean 0.49, p10 0.202, p90 0.784 (final; Sentinel-2 L2A).
t2m over 2026-09-03/2026-10-03: mean 15.8, p10 12.6, p90 18.8 degC (preliminary; ERA5-Land hourly time series).
precipitation over 2026-09-03/2026-10-03: total 48.7 mm (preliminary; ERA5-Land hourly time series).
no2_column over 2026-08-11/2026-10-10: mean 38.6, p10 27.7, p90 49.3 umol m-2 (final; Sentinel-5P TROPOMI L2 (offline)).
```

### Population, buildings, land-cover history, solar, climate to 2050 and air history (Smolec)

Call:

```json
{
  "tool": "get_summary",
  "arguments": {
    "area_id": "a_pt_51.0730_16.8990_ci1000",
    "variables": [
      "population_grid",
      "building_functions",
      "landcover_history",
      "solar_potential",
      "... 2 more"
    ]
  }
}
```

Structured content:

```json
{
  "area": {
    "area_id": "a_pt_51.0730_16.8990_ci1000",
    "name": "Circle of radius 1.00 km around 51.0730, 16.8990",
    "kind": "buffer",
    "area_km2": 3.136548,
    "bbox": [
      16.884732,
      51.064011,
      16.913268,
      51.081989
    ],
    "geometry": {
      "type": "Polygon",
      "coordinates": "... (simplified to at most ~200 vertices)"
    },
    "admin": {},
    "source": "user coordinates",
    "licence": "n/a"
  },
  "facts": [
    {
      "variable": "population_grid",
      "units": "persons",
      "value": {
        "2000": 717.0,
        "2010": 997.0,
        "2020": 1240.0,
        "2025": 1352.0
      },
      "period": "2000/2025",
      "status": "final",
      "provenance": {
        "source": "JRC GHSL GHS-POP R2023A (100 m)",
        "backend": "pixels",
        "collection": "GHS_POP_E{2000,2010,2020,2025}_GLOBE_R2023A_54009_100",
        "method": "sum of 100 m cells whose centre lies inside the area; 2025 is a model projection, 2020 the last epoch based on census inputs",
        "licence": "CC-BY-4.0",
        "attribution": "European Commission, Joint Research Centre (JRC), GHS-POP R2023A",
        "commercial_safe": true,
        "retrieved_at": "2026-10-10T21:57:55.474209Z"
      }
    },
    {
      "variable": "building_functions",
      "units": "count (footprint in m², floors as a mean)",
      "value": {
        "residential": 585.0,
        "agricultural": 80.0,
        "retail_services": 7.0,
        "industrial": 6.0,
        "other_non_residential": 5.0,
        "storage": 3.0,
        "offices": 1.0,
        "education_culture_sport": 1.0,
        "total": 688.0,
        "footprint_m2": 111080.0,
        "mean_floors": 1.5
      },
      "period": null,
      "status": "final",
      "provenance": {
        "source": "GUGiK BDOT10k topographic database (buildings)",
        "backend": "per-powiat GML download, parsed once and cached",
        "collection": "OT_BUBD_A (existing buildings)",
        "method": "buildings whose outline centroid lies inside the area, by general function (funkcjaOgolnaBudynku)",
        "licence": "open data (PZGiK)",
        "attribution": "Główny Urząd Geodezji i Kartografii, BDOT10k",
        "commercial_safe": true,
        "retrieved_at": "2026-10-10T21:57:55.474307Z"
      }
    },
    {
      "variable": "landcover_history",
      "units": "fraction",
      "value": {
        "urban_fabric_1990": 0.0995,
        "agricultural_1990": 0.9009,
        "urban_fabric_2000": 0.1296,
        "agricultural_2000": 0.8704,
        "urban_fabric_2006": 0.1802,
        "agricultural_2006": 0.8204,
        "urban_fabric_2012": 0.2289,
        "agricultural_2012": 0.7716,
        "urban_fabric_2018": 0.2487,
        "agricultural_2018": 0.7515
      },
      "period": "1990/2018",
      "status": "final",
      "provenance": {
        "source": "CORINE Land Cover 1990-2018 (EEA)",
        "backend": "server_stats",
        "collection": "CLC 1990, 2000, 2006, 2012, 2018",
        "method": "CORINE polygons intersected with the area (EPSG:3035), area share per group and year; changes under 5 ha are not mapped, so small shifts are noise",
        "licence": "Copernicus Land Monitoring Service data policy (free, attribution)",
        "attribution": "© European Union, Copernicus Land Monitoring Service, European Environment Agency (EEA)",
        "commercial_safe": true,
        "retrieved_at": "2026-10-10T21:57:55.475238Z"
      }
    },
    {
      "variable": "solar_potential",
      "units": "kWh per kWp per year",
      "value": {
        "yield_optimal_kwh_per_kwp": 1107.2,
        "yield_flat_kwh_per_kwp": 912.1,
        "optimal_tilt_deg": 41.0,
        "irradiation_optimal_kwh_m2": 1380.4
      },
      "period": "2005/2023",
      "status": "final",
      "provenance": {
        "source": "JRC PVGIS 5.3",
        "backend": "server_stats",
        "collection": "PVGIS-SARAH3",
        "method": "PVcalc at the area centroid, 1 kWp free-standing crystalline silicon, 14% system losses, radiation 2005-2023 (PVGIS-SARAH3)",
        "licence": "CC-BY-4.0",
        "attribution": "European Commission, Joint Research Centre, PVGIS",
        "commercial_safe": true,
        "retrieved_at": "2026-10-10T21:57:55.476198Z"
      }
    },
    "... 2 more"
  ],
  "gaps": [],
  "notices": [],
  "cost": {
    "duration_s": 0.02,
    "sentinel_hub_units": 0.0,
    "cache_hits": 5,
    "cache_misses": 0
  }
}
```

Text:

```
Circle of radius 1.00 km around 51.0730, 16.8990 (3.1 km²):
population_grid over 2000/2025: 2000 717, 2010 997, 2020 1,240, 2025 1,352 persons (final; JRC GHSL GHS-POP R2023A (100 m)).
building_functions: residential 585, agricultural 80, retail services 7, industrial 6, other non residential 5, storage 3, offices 1, education culture sport 1, total 688, footprint m2 111,080, mean floors 1.5 count (footprint in m², floors as a mean) (final; GUGiK BDOT10k topographic database (buildings)).
landcover_history over 1990/2018: urban fabric 1990 10%, agricultural 1990 90%, urban fabric 2000 13%, agricultural 2000 87%, urban fabric 2006 18%, agricultural 2006 82%, urban fabric 2012 23%, agricultural 2012 77%, urban fabric 2018 25%, agricultural 2018 75% (final; CORINE Land Cover 1990-2018 (EEA)).
solar_potential over 2005/2023: yield optimal kwh per kwp 1,107, yield flat kwh per kwp 912, optimal tilt deg 41, irradiation optimal kwh m2 1,380 kWh per kWp per year (final; JRC PVGIS 5.3).
climate_projection over 2031/2050: tmax mean 1991 2010 13.2, tmax mean 2031 2050 14.7, tmax change 1.5, hot days 1991 2010 5.8, hot days 2031 2050 13, precip mm 1991 2010 682, precip mm 2031 2050 663, precip change pct -2.7, models 3 mixed (°C, days per year, mm per year) (final; Open-Meteo Climate API (CMIP6 HighResMIP, downscaled)).
pm10_history over 2012/2024: 2012 39.4, 2013 36.3, 2014 32.7, 2015 28.5, 2016 27.9, 2017 28.7, 2018 29.1, 2019 21.9, 2020 22, 2021 24.3, 2022 23.8, 2023 20.7, 2024 22.9 ug m-3 (final; GIOŚ air-quality monitoring stations).
```

## compare_areas

### Two gminas, summer NDVI

Call:

```json
{
  "tool": "compare_areas",
  "arguments": {
    "area_ids": [
      "a_osm_R3048177",
      "a_osm_R3049635"
    ],
    "variable": "ndvi",
    "start": "2025-06-01",
    "end": "2025-09-01"
  }
}
```

Structured content:

```json
{
  "variable": "ndvi",
  "units": "1",
  "start": "2025-06-01",
  "end": "2025-09-01",
  "aggregation": "mean over the whole period",
  "ranking": [
    {
      "rank": 1,
      "area_id": "a_osm_R3048177",
      "name": "gmina Kąty Wrocławskie",
      "area_km2": 176.717125,
      "value": 0.604564,
      "valid_fraction": 0.375,
      "status": "final"
    },
    {
      "rank": 2,
      "area_id": "a_osm_R3049635",
      "name": "gmina Kobierzyce",
      "area_km2": 149.20352,
      "value": 0.583591,
      "valid_fraction": 0.398,
      "status": "final"
    }
  ],
  "provenance": {
    "source": "Sentinel-2 L2A",
    "backend": "sentinel-hub-statistical",
    "collection": "sentinel-2-l2a",
    "method": "per acquisition: mean over cloud-free pixels (SCL classes 4-6) at 150 m (bilinear); acquisitions with under 20% cloud-free area dropped; period value = mean of acquisition means",
    "licence": "Copernicus open data (CC-BY-4.0)",
    "attribution": "Contains modified Copernicus Sentinel data",
    "commercial_safe": true,
    "retrieved_at": "2026-10-10T21:57:55.538107Z"
  },
  "notices": [],
  "cost": {
    "duration_s": 0.04,
    "sentinel_hub_units": 0.0,
    "cache_hits": 2,
    "cache_misses": 0
  }
}
```

Text:

```
ndvi, mean over the whole period, 2025-06-01 to 2025-09-01: gmina Kąty Wrocławskie 0.605 > gmina Kobierzyce 0.584.
```

## get_snapshot

### NDVI map

Call:

```json
{
  "tool": "get_snapshot",
  "arguments": {
    "area_id": "a_pt_51.1100_17.0320_sq1581",
    "layer": "ndvi",
    "date": "2025-07-19"
  }
}
```

Content: an MCP image block (image/png) and a text block.

Structured content:

```json
{
  "layer": "ndvi",
  "scene_date": "2025-07-05",
  "requested_date": "2025-07-19",
  "scene_cloud_cover": 0.136,
  "size_px": [
    315,
    316
  ],
  "pixel_size_m": 10.0,
  "legend": {
    "colormap": "brown-yellow-green",
    "range": [
      -0.2,
      0.9
    ],
    "units": "1"
  },
  "provenance": {
    "source": "sentinel-2-l2a",
    "backend": "sentinel-hub-process",
    "collection": "sentinel-2-l2a",
    "method": "least-cloudy scene within 15 days of the requested date",
    "licence": "Copernicus open data (CC-BY-4.0)",
    "attribution": "Contains modified Copernicus Sentinel data",
    "commercial_safe": true,
    "retrieved_at": "2026-10-10T21:57:56.937539Z"
  }
}
```

Text:

```
ndvi for a_pt_51.1100_17.0320_sq1581: scene 2025-07-05, tile cloud 14%, 315x316 px at 10.0 m. Legend: {'colormap': 'brown-yellow-green', 'range': [-0.2, 0.9], 'units': '1'}
```

### Aerial photo of a parcel

Call:

```json
{
  "tool": "get_snapshot",
  "arguments": {
    "area_id": "a_uldk_026401_1.0001.2395",
    "layer": "orthophoto"
  }
}
```

Content: an MCP image block (image/png) and a text block.

Structured content:

```json
{
  "layer": "orthophoto",
  "scene_date": null,
  "requested_date": null,
  "scene_cloud_cover": null,
  "size_px": [
    159,
    121
  ],
  "pixel_size_m": 0.25,
  "legend": {
    "type": "aerial photo",
    "note": "most recent national orthophoto; flight year varies by region"
  },
  "provenance": {
    "source": "GUGiK orthophoto (PZGiK)",
    "backend": "gugik-wms",
    "collection": "ORTO",
    "method": "WMS GetMap from HighResolution",
    "licence": "open data (PZGiK, free of charge since 2020)",
    "attribution": "Główny Urząd Geodezji i Kartografii",
    "commercial_safe": true,
    "retrieved_at": "2026-10-10T21:57:57.634261Z"
  }
}
```

Text:

```
orthophoto for a_uldk_026401_1.0001.2395: GUGiK orthophoto (PZGiK), 159x121 px at 0.25 m. Legend: {'type': 'aerial photo', 'note': 'most recent national orthophoto; flight year varies by region'}
```

## describe_data

### Hazard sources, and which cover an area

Call:

```json
{
  "tool": "describe_data",
  "arguments": {
    "topic": "hazard",
    "area_id": "a_pt_51.1100_17.0320_sq1581"
  }
}
```

Structured content:

```json
{
  "categories": {
    "Hazards and constraints": [
      {
        "entry": {
          "id": "isok_flood",
          "category": "Hazards and constraints",
          "measures": "Share of the area in official flood hazard zones: once in 10, 100 and 500 years, and if a flood embankment fails",
          "region": "Poland",
          "history": "current official maps (2020 cycle)",
          "update_frequency": "every 6 years",
          "tools": [
            "get_constraints"
          ],
          "limits": "areas up to 250 km²",
          "cost": "free",
          "examples": [
            "Czy ta działka leży w strefie zagrożenia powodzią?",
            "How much of this gmina is flood-prone?"
          ],
          "caveats": [
            "Zones include the river bed itself; land behind embankments counts only in the failure scenario"
          ]
        },
        "name": "ISOK flood hazard maps (Wody Polskie)",
        "variables": [
          "flood zone shares"
        ],
        "licence": "public data",
        "commercial_safe": true,
        "health": "ok",
        "available_here": true
      },
      {
        "entry": {
          "id": "gdos_protected",
          "category": "Hazards and constraints",
          "measures": "Nature protection: Natura 2000 sites, national and landscape parks, reserves, protected landscape areas, ecological use areas, with names and shares of the area",
          "region": "Poland",
          "history": "current register",
          "update_frequency": "continuous",
          "tools": [
            "get_constraints"
          ],
          "limits": "",
          "cost": "free",
          "examples": [
            "Czy na tym terenie są obszary Natura 2000?",
            "Which protected areas overlap this site?"
          ],
          "caveats": []
        },
        "name": "GDOŚ nature protection register",
        "variables": [
          "protected area shares"
        ],
        "licence": "public data",
        "commercial_safe": true,
        "health": "ok",
        "available_here": true
      },
      {
        "entry": {
          "id": "pig_geohazards",
          "category": "Hazards and constraints",
          "measures": "Landslides and landslide-prone areas, mining terrains and mining areas, mineral deposits, and vertical ground motion (subsidence) in mm per year",
          "region": "Poland",
          "history": "current registers; ground motion 2016-2020 (EGMS)",
          "update_frequency": "registers continuous; ground motion yearly releases",
          "tools": [
            "get_constraints"
          ],
          "limits": "",
          "cost": "free",
          "examples": [
            "Czy grunt w tej okolicy osiada przez kopalnie?",
            "Are there landslides or mining areas here?"
          ],
          "caveats": [
            "Ground motion is interpolated between radar points; an indication, not a survey"
          ]
        },
        "name": "PIG-PIB geohazards and EGMS ground motion",
        "variables": [
          "hazard shares"
        ],
        "licence": "public data",
        "commercial_safe": true,
        "health": "ok",
        "available_here": true
      },
      {
        "entry": {
          "id": "gios_noise",
          "category": "Hazards and constraints",
          "measures": "Road, rail, aircraft and industrial noise: share of the area at Lden 55 dB or more (annoyance) and 65 dB or more (health risk)",
          "region": "Poland (major roads, railways and airports, and cities over 100,000 people)",
          "history": "strategic noise maps 2017 (round 3)",
          "update_frequency": "every 5 years",
          "tools": [
            "get_constraints"
          ],
          "limits": "",
          "cost": "free",
          "examples": [
            "Czy ta działka jest w zasięgu hałasu z lotniska?",
            "How noisy is this street from traffic?"
          ],
          "caveats": [
            "Modelled maps from 2017; newer 2022 maps are not available as a queryable service"
          ]
        },
        "name": "GIOŚ strategic noise maps 2017",
        "variables": [
          "noise shares"
        ],
        "licence": "CC-BY-4.0",
        "commercial_safe": true,
        "health": "ok",
        "available_here": true
      },
      "... 2 more"
    ]
  },
  "planned": [
    "Official boundaries (PRG) and TERYT registers",
    "Company details from GUS REGON (key requested)",
    "Europe: ground motion outside Poland (EGMS, needs an EU Login token)"
  ],
  "area": "3.16 km square around 51.1100, 17.0320"
}
```

Text:

```
Data available for 3.16 km square around 51.1100, 17.0320:

Hazards and constraints:
- ISOK flood hazard maps (Wody Polskie): Share of the area in official flood hazard zones: once in 10, 100 and 500 years, and if a flood embankment fails. Poland; current official maps (2020 cycle); updated every 6 years. Tools: get_constraints. Example: Czy ta działka leży w strefie zagrożenia powodzią?
- GDOŚ nature protection register: Nature protection: Natura 2000 sites, national and landscape parks, reserves, protected landscape areas, ecological use areas, with names and shares of the area. Poland; current register; updated continuous. Tools: get_constraints. Example: Czy na tym terenie są obszary Natura 2000?
- PIG-PIB geohazards and EGMS ground motion: Landslides and landslide-prone areas, mining terrains and mining areas, mineral deposits, and vertical ground motion (subsidence) in mm per year. Poland; current registers; ground motion 2016-2020 (EGMS); updated registers continuous; ground motion yearly releases. Tools: get_constraints. Example: Czy grunt w tej okolicy osiada przez kopalnie?
- GIOŚ strategic noise maps 2017: Road, rail, aircraft and industrial noise: share of the area at Lden 55 dB or more (annoyance) and 65 dB or more (health risk). Poland (major roads, railways and airports, and cities over 100,000 people); strategic noise maps 2017 (round 3); updated every 5 years. Tools: get_constraints. Example: Czy ta działka jest w zasięgu hałasu z lotniska?
- IUNG agricultural drought monitoring: Agricultural drought risk this season for the gmina: which crops were at risk in which 10-day periods, per soil category, with the soil category under the area. Poland; current season (2020 onwards on the IUNG site); updated every 10 days, March to September. Tools: get_constraints. Example: Czy w tej gminie jest susza rolnicza?
- IMGW-PIB warnings: Current IMGW weather warnings for the powiat and hydrological warnings (floods, drought) for the voivodeship. Poland; current only; updated continuous. Tools: get_constraints. Example: Czy są teraz ostrzeżenia pogodowe dla tej gminy?

Coming next: Official boundaries (PRG) and TERYT registers; Company details from GUS REGON (key requested); Europe: ground motion outside Poland (EGMS, needs an EU Login token).
```

## find_statistics

### Search official indicators (Polish words)

Call:

```json
{
  "tool": "find_statistics",
  "arguments": {
    "query": "wynagrodzenie"
  }
}
```

Structured content:

```json
{
  "query": "wynagrodzenie",
  "indicators": [
    {
      "id": "bdl:1749925",
      "source": "GUS BDL",
      "name": "Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: styczeń / wg miejsca zamieszkania / ogółem",
      "unit": "zł",
      "level": "gmina",
      "topic": "P4609"
    },
    {
      "id": "bdl:1749928",
      "source": "GUS BDL",
      "name": "Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: styczeń / wg siedziby podmiotu / ogółem",
      "unit": "zł",
      "level": "gmina",
      "topic": "P4609"
    },
    {
      "id": "bdl:1749931",
      "source": "GUS BDL",
      "name": "Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: luty / wg miejsca zamieszkania / ogółem",
      "unit": "zł",
      "level": "gmina",
      "topic": "P4609"
    },
    {
      "id": "bdl:1749934",
      "source": "GUS BDL",
      "name": "Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: luty / wg siedziby podmiotu / ogółem",
      "unit": "zł",
      "level": "gmina",
      "topic": "P4609"
    },
    "... 31 more"
  ]
}
```

Text:

```
35 indicator(s) for 'wynagrodzenie':
- bdl:1749925 [gmina, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: styczeń / wg miejsca zamieszkania / ogółem
- bdl:1749928 [gmina, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: styczeń / wg siedziby podmiotu / ogółem
- bdl:1749931 [gmina, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: luty / wg miejsca zamieszkania / ogółem
- bdl:1749934 [gmina, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: luty / wg siedziby podmiotu / ogółem
- bdl:1749937 [gmina, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: marzec / wg miejsca zamieszkania / ogółem
- bdl:1749940 [gmina, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: marzec / wg siedziby podmiotu / ogółem
- bdl:1749943 [gmina, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: kwiecień / wg miejsca zamieszkania / ogółem
- bdl:1749946 [gmina, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: kwiecień / wg siedziby podmiotu / ogółem
- bdl:1749949 [gmina, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: maj / wg miejsca zamieszkania / ogółem
- bdl:1749952 [gmina, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: maj / wg siedziby podmiotu / ogółem
- bdl:1749926 [powiat, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: styczeń / wg miejsca zamieszkania / mężczyźni
- bdl:1749927 [powiat, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: styczeń / wg miejsca zamieszkania / kobiety
- bdl:1749929 [powiat, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: styczeń / wg siedziby podmiotu / mężczyźni
- bdl:1749930 [powiat, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: styczeń / wg siedziby podmiotu / kobiety
- bdl:1749932 [powiat, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: luty / wg miejsca zamieszkania / mężczyźni
- bdl:1749933 [powiat, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: luty / wg miejsca zamieszkania / kobiety
- bdl:1749935 [powiat, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: luty / wg siedziby podmiotu / mężczyźni
- bdl:1749936 [powiat, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: luty / wg siedziby podmiotu / kobiety
- bdl:1749938 [powiat, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: marzec / wg miejsca zamieszkania / mężczyźni
- bdl:1749939 [powiat, zł] Przeciętne miesięczne wynagrodzenie brutto według badania Rozkład wynagrodzeń w gospodarce narodowej: marzec / wg miejsca zamieszkania / kobiety
- smup:599 [gmina] Przeciętne miesięczne wynagrodzenie brutto  (Ogółem)
- smup:1609 [gmina] Przeciętne wynagrodzenie rodziny zastępczej zawodowej (Ogółem)
- smup:1610 [gmina] Przeciętne wynagrodzenie rodzinnego domu dziecka (Ogółem)
- smup:4027 [gmina] Wydatki bieżące na wynagrodzenia i składki od nich naliczane w budżetach gmin (Ogółem)
- smup:4052 [gmina] Udział wydatków na wynagrodzenia w wydatkach bieżących budżetów gmin (Ogółem)
- smup:4064 [gmina] Wydatki bieżące budżetów gmin na wynagrodzenia i składki od nich naliczane - na 1 mieszkańca (Ogółem)
- smup:4102 [gmina] Wydatki bieżące na wynagrodzenia i składki od nich naliczane w budżetach powiatów (Ogółem)
- smup:4127 [gmina] Udział wydatków na wynagrodzenia w wydatkach bieżących budżetów powiatów (Ogółem)
- smup:4139 [gmina] Wydatki bieżące budżetów powiatów na wynagrodzenia i składki od nich naliczane na 1 mieszkańca (Ogółem)
- smup:4177 [gmina] Wydatki bieżące na wynagrodzenia i składki od nich naliczane w budżetach samorządów województw (Ogółem)
- smup:4202 [gmina] Udział wydatków na wynagrodzenia w wydatkach bieżących budżetów samorządów województw (Ogółem)
- smup:4214 [gmina] Wydatki bieżące budżetów samorządów województw na wynagrodzenia i składki od nich naliczane na 1 mieszkańca (Ogółem)
- smup:4377 [gmina] Wydatki bieżące na wynagrodzenia i składki od nich naliczane w budżetach miast na prawach powiatu (Ogółem)
- smup:4402 [gmina] Udział wydatków na wynagrodzenia w wydatkach bieżących budżetów miast na prawach powiatu (Ogółem)
- smup:4414 [gmina] Wydatki bieżące budżetów miast na prawach powiatu na wynagrodzenia i składki od nich naliczane - na 1 mieszkańca (Ogółem)
```

## get_statistics

### Population, compared with powiat, voivodeship and Poland

Call:

```json
{
  "tool": "get_statistics",
  "arguments": {
    "area_id": "a_osm_R3048177",
    "indicator_ids": [
      "bdl:72305"
    ],
    "years": [
      2020,
      2023
    ]
  }
}
```

Structured content:

```json
{
  "area": "gmina Kąty Wrocławskie",
  "gmina": "Kąty Wrocławskie",
  "units": {
    "gmina": "Kąty Wrocławskie",
    "powiat": "Powiat wrocławski",
    "voivodeship": "DOLNOŚLĄSKIE",
    "Poland": "Polska"
  },
  "indicators": [
    {
      "indicator": {
        "id": "bdl:72305",
        "source": "GUS BDL",
        "name": "Ludność wg grup wieku i płci: ogółem / ogółem",
        "unit": "osoba",
        "level": "gmina",
        "topic": "P2137"
      },
      "values": {
        "gmina": {
          "2020": 28554.0,
          "2023": 30202.0
        },
        "powiat": {
          "2020": 174276.0,
          "2023": 187153.0
        },
        "voivodeship": {
          "2020": 2908378.0,
          "2023": 2879271.0
        },
        "Poland": {
          "2020": 38088564.0,
          "2023": 37636508.0
        }
      }
    }
  ],
  "notices": []
}
```

Text:

```
Kąty Wrocławskie (Powiat wrocławski, DOLNOŚLĄSKIE):
bdl:72305 Ludność wg grup wieku i płci: ogółem / ogółem [osoba]:
  gmina: 2020 28,554, 2023 30,202
  powiat: 2020 174,276, 2023 187,153
  voivodeship: 2020 2,908,378, 2023 2,879,271
  Poland: 2020 38,088,564, 2023 37,636,508
```

## get_constraints

### Floodplain district in Wrocław

Call:

```json
{
  "tool": "get_constraints",
  "arguments": {
    "area_id": "a_pt_51.1450_16.9550_ci500"
  }
}
```

Structured content:

```json
{
  "area": "Circle of radius 0.50 km around 51.1450, 16.9550",
  "area_km2": 0.784137,
  "items": [
    {
      "key": "flood_10pct",
      "category": "flood",
      "label": "Flood zone, 10% a year (once in 10 years)",
      "share": 0.0834,
      "count": 0,
      "names": [],
      "value": {},
      "source": "ISOK flood hazard maps (Wody Polskie)",
      "licence": "public data (Wody Polskie)"
    },
    {
      "key": "flood_1pct",
      "category": "flood",
      "label": "Flood zone, 1% a year (once in 100 years)",
      "share": 0.236,
      "count": 0,
      "names": [],
      "value": {},
      "source": "ISOK flood hazard maps (Wody Polskie)",
      "licence": "public data (Wody Polskie)"
    },
    {
      "key": "flood_02pct",
      "category": "flood",
      "label": "Flood zone, 0.2% a year (once in 500 years)",
      "share": 0.4059,
      "count": 0,
      "names": [],
      "value": {},
      "source": "ISOK flood hazard maps (Wody Polskie)",
      "licence": "public data (Wody Polskie)"
    },
    {
      "key": "flood_embankment_failure",
      "category": "flood",
      "label": "Flood zone if a flood embankment fails",
      "share": 0.2217,
      "count": 0,
      "names": [],
      "value": {},
      "source": "ISOK flood hazard maps (Wody Polskie)",
      "licence": "public data (Wody Polskie)"
    },
    "... 21 more"
  ],
  "gaps": [],
  "notices": [
    "Ground motion is interpolated between radar points (2016-2020); treat it as an indication."
  ],
  "retrieved_at": "2026-10-10T21:58:00.339601Z"
}
```

Text:

```
Circle of radius 0.50 km around 51.1450, 16.9550 (78.4 ha):
- Flood zone, 10% a year (once in 10 years): 8% of the area
- Flood zone, 1% a year (once in 100 years): 24% of the area
- Flood zone, 0.2% a year (once in 500 years): 41% of the area
- Flood zone if a flood embankment fails: 22% of the area
- Natura 2000 habitat site (SOO): 14% of the area; Las Pilczycki - PLH020069
- Vertical ground motion 2016-2020 (EGMS, interpolated), mm/year; negative is subsidence: mean -0.1, median 0.0, min -1.0, max 0.0
- Road noise, Lden 55 dB or more: 27% of the area (65 dB or more: 8%)
- IMGW hydrological warnings in dolnośląskie (floods, drought): 13 active: dolnośląskie,  Odra od ujścia Widawy do Ścinawy, zlewnia Kaczawy, susza hydrologiczna (level -1); dolnośląskie, Czerna Wielka, susza hydrologiczna (level -1); dolnośląskie, Odra od Głogowa do ujścia Bobru, susza hydrologiczna (level -1); dolnośląskie, Odra skanalizowana (od Koźla do Malczyc), susza hydrologiczna (level -1); dolnośląskie, Odra środkowa poniżej Malczyc do ujścia Bobru, susza hydrologiczna (level -1); dolnośląskie, Zlewnia Bobru z Kwisą (od Zb. Pilchowice do ujścia,bez zlewni Czernej Wielkiej, susza hydrologiczna (level -1); dolnośląskie, Zlewnia Bystrzycy, susza hydrologiczna (level -1); dolnośląskie, Zlewnia Widawy, Stobrawy, Odra od ujścia Nysy Kłodzkiej do ujścia Widawy, susza hydrologiczna (level -1); dolnośląskie, Zlewnia Ślęzy i Oławy, susza hydrologiczna (level -1); dolnośląskie, rzeka Bystrzyca od Zb. Mietków do ujścia, susza hydrologiczna (level -1); dolnośląskie, zlewnia górnego Bobru i zlewnia Ostrożnicy w granicach RP, susza hydrologiczna (level -1); dolnośląskie, zlewnie Nysy Kłodzkiej powyżej Zbiornika Nysa i dopływów Łaby w granicach RP, susza hydrologiczna (level -1)
- IUNG agricultural drought risk for the gmina in 2026 (crops and 10-day periods at risk): 18 active: soil category I: Zboża ozime (periods 02); soil category I: Zboża jare (periods 02); soil category I: Kukurydza na ziarno (periods 09, 10); soil category I: Kukurydza na kiszonkę (periods 09, 10); soil category I: Rzepak i rzepik (periods 06); soil category I: Ziemniak (periods 10); soil category I: Chmiel (periods 10); soil category I: Tytoń (periods 10); soil category I: Warzywa gruntowe (periods 10); soil category I: Krzewy owocowe (periods 02, 09, 10); soil category I: Truskawki (periods 02); soil category I: Rośliny strączkowe (periods 09, 10)
None found: Natura 2000 bird site (OSO); National park; Landscape park; Nature reserve; Protected landscape area; Ecological use area (użytek ekologiczny); Nature and landscape complex; Landslides (SOPO); Areas threatened by mass movement (SOPO); Mining terrain (teren górniczy); Mining area (obszar górniczy); Documented mineral deposit (złoże); Rail noise, Lden 55 dB or more; Aircraft noise, Lden 55 dB or more; Industrial noise, Lden 55 dB or more; IMGW weather warnings for the powiat.
Notice: Ground motion is interpolated between radar points (2016-2020); treat it as an indication.
```

## get_prices

### Old-town apartments since 2025

Call:

```json
{
  "tool": "get_prices",
  "arguments": {
    "area_id": "a_pt_51.1080_17.0400_ci200",
    "since_year": 2025
  }
}
```

Structured content:

```json
{
  "area": "Circle of radius 0.20 km around 51.1080, 17.0400",
  "area_km2": 0.125462,
  "series": [
    {
      "kind": "apartments_primary",
      "unit": "PLN/m²",
      "description": "Apartments, new from developers: price per m² of floor",
      "years": {}
    },
    {
      "kind": "apartments_secondary",
      "unit": "PLN/m²",
      "description": "Apartments, resale: price per m² of floor",
      "years": {
        "2025": {
          "count": 4,
          "median": 16236.0,
          "p25": 14707.0,
          "p75": 17890.0
        },
        "2026": {
          "count": 3,
          "median": 17661.0,
          "p25": 15251.0,
          "p75": 19808.0
        }
      }
    },
    {
      "kind": "houses",
      "unit": "PLN",
      "description": "Houses (a plot with one residential building): price per transaction",
      "years": {}
    },
    {
      "kind": "land",
      "unit": "PLN/m²",
      "description": "Undeveloped plots: price per m² of land",
      "years": {}
    }
  ],
  "transactions_read": {
    "lokale": 17,
    "budynki": 17,
    "dzialki": 49
  },
  "notices": [
    "Gross prices from notarial deeds, free-market sales only; years with under 3 sales are omitted."
  ],
  "source": "Rejestr Cen Nieruchomości (GUGiK, powiat registers)",
  "licence": "open data (PZGiK)"
}
```

Text:

```
Circle of radius 0.20 km around 51.1080, 17.0400 (12.5 ha), Rejestr Cen Nieruchomości (GUGiK, powiat registers):
- Apartments, resale: price per m² of floor, median PLN/m²: 2025: 16,236 (n=4); 2026: 17,661 (n=3)
Notice: Gross prices from notarial deeds, free-market sales only; years with under 3 sales are omitted.
```

## get_zoning

### A point in Kraków's Kazimierz plan

Call:

```json
{
  "tool": "get_zoning",
  "arguments": {
    "area_id": "a_pt_50.0510_19.9450_ci50"
  }
}
```

Structured content:

```json
{
  "area": "Circle of radius 0.05 km around 50.0510, 19.9450",
  "points_sampled": 1,
  "points_in_a_plan": 1,
  "designations": [
    {
      "symbol": "MW/U.31",
      "description": "Tereny zabudowy mieszkaniowo - usługowej (MW/U.1-MW/U.57)",
      "plan": "KAZIMIERZ",
      "resolution": "LXXXVIII/2145/17",
      "adopted": "08.11.2017",
      "link": "http://www.bip.krakow.pl/?dok_id=92971",
      "share_of_points": 1.0,
      "attributes": {
        "Uchwalenie": "LXXXVIII/2145/17",
        "Data uchwalenia": "08.11.2017",
        "Ogłoszenie": "2017.7618",
        "Data DUWM": "23.11.2017",
        "Data obowiązywania": "08.12.2017",
        "Oznaczenie": "MW/U.31",
        "Nazwa MPZP": "KAZIMIERZ",
        "WWW": "http://www.bip.krakow.pl/?dok_id=92971",
        "Rodzaj oznaczenia": "MW/U",
        "opis_oznac": "Tereny zabudowy mieszkaniowo - usługowej (MW/U.1-MW/U.57)",
        "Data zasilenia": "2026-10-08"
      }
    }
  ],
  "no_plan_messages": [],
  "parcel": null,
  "gaps": [],
  "notices": [
    "Designations are sampled at points; read the plan text (link) before any decision."
  ],
  "retrieved_at": "2026-10-10T21:58:00.385871Z"
}
```

Text:

```
Circle of radius 0.05 km around 50.0510, 19.9450: 1 of 1 sample points lie in a local plan.
- MW/U.31 - Tereny zabudowy mieszkaniowo - usługowej (MW/U.1-MW/U.57) (100% of points); KAZIMIERZ, adopted 08.11.2017, http://www.bip.krakow.pl/?dok_id=92971
Notice: Designations are sampled at points; read the plan text (link) before any decision.
```

### A Wrocław parcel (no plan in the national service)

Call:

```json
{
  "tool": "get_zoning",
  "arguments": {
    "area_id": "a_uldk_026401_1.0001.2395"
  }
}
```

Structured content:

```json
{
  "area": "Parcel 026401_1.0001.2395",
  "points_sampled": 1,
  "points_in_a_plan": 0,
  "designations": [],
  "no_plan_messages": [
    "m. Wrocław: brak wyniku dla wskazanego obszaru"
  ],
  "parcel": {
    "registry_area_ha": "0.0618",
    "register_group": "15",
    "land_use_contour": "Bi"
  },
  "gaps": [],
  "notices": [
    "Designations are sampled at points; read the plan text (link) before any decision.",
    "No plan found. Either no local plan covers this area, or the gmina does not publish its plans to the national integration (Wrocław, for example); check the gmina's own geoportal."
  ],
  "retrieved_at": "2026-10-10T21:58:01.066121Z"
}
```

Text:

```
Parcel 026401_1.0001.2395: 0 of 1 sample points lie in a local plan.
No plan: m. Wrocław: brak wyniku dla wskazanego obszaru
Cadastre: registry area ha 0.0618, register group 15, land use contour Bi
Notice: Designations are sampled at points; read the plan text (link) before any decision.
Notice: No plan found. Either no local plan covers this area, or the gmina does not publish its plans to the national integration (Wrocław, for example); check the gmina's own geoportal.
```

## get_traffic

### The A4 south of Kąty Wrocławskie

Call:

```json
{
  "tool": "get_traffic",
  "arguments": {
    "area_id": "a_pt_51.0230_16.7700_ci1500"
  }
}
```

Structured content:

```json
{
  "area": "Circle of radius 1.50 km around 51.0230, 16.7700",
  "survey": "GPR 2025",
  "roads_in_area": [
    "A4",
    "346",
    "347",
    "5"
  ],
  "segments": [
    {
      "road": "5",
      "name": "W. KOSTOMŁOTY /A4/ - MIELĘCIN /DP3396D/",
      "km_from": 370.438,
      "km_to": 380.143,
      "vehicles_per_day": 16365,
      "vehicles_per_day_2020": 17442,
      "cars_per_day": 12702,
      "heavy_share": 0.135,
      "measured_at": "Wichrów",
      "distance_km": 11.2
    },
    {
      "road": "A4",
      "name": "W. KOSTOMŁOTY /DK5/ - W. KĄTY WR. /DW347/",
      "km_from": 125.9,
      "km_to": 137.585,
      "vehicles_per_day": 62876,
      "vehicles_per_day_2020": 59432,
      "cars_per_day": 41980,
      "heavy_share": 0.236,
      "measured_at": "Kąty Wr.",
      "distance_km": 0.0
    },
    {
      "road": "A4",
      "name": "W. KĄTY WR. /DW347/ - W. PIETRZYKOWICE",
      "km_from": 137.585,
      "km_to": 147.691,
      "vehicles_per_day": 69288,
      "vehicles_per_day_2020": 62776,
      "cars_per_day": 47278,
      "heavy_share": 0.224,
      "measured_at": "Kąty Wr.",
      "distance_km": 0.0
    }
  ],
  "toll_trends": [
    {
      "road": "A4",
      "last_12_months": 262284214,
      "change_vs_year_before": 0.01,
      "latest_month": "2026-07"
    }
  ],
  "notices": [
    "GPR 2025 average daily traffic on national roads outside city limits; per road, the sections measured inside the area, or else the nearest measured section (up to 15 km away)."
  ],
  "source": "GDDKiA Generalny Pomiar Ruchu 2025 and 2020/21 (dane.gov.pl); road refs © OpenStreetMap",
  "licence": "CC0 1.0 (GDDKiA); ODbL-1.0 (OSM)",
  "retrieved_at": "2026-10-10T21:58:01.207650Z"
}
```

Text:

```
Circle of radius 1.50 km around 51.0230, 16.7700: national roads A4, 346, 347, 5 (GPR 2025).
- 5 W. KOSTOMŁOTY /A4/ - MIELĘCIN /DP3396D/: 16,365 vehicles/day, -6% since 2020/21, heavy 14% (measured 11.2 km away, Wichrów)
- A4 W. KOSTOMŁOTY /DK5/ - W. KĄTY WR. /DW347/: 62,876 vehicles/day, +6% since 2020/21, heavy 24% (measured inside the area, Kąty Wr.)
- A4 W. KĄTY WR. /DW347/ - W. PIETRZYKOWICE: 69,288 vehicles/day, +10% since 2020/21, heavy 22% (measured inside the area, Kąty Wr.)
- e-TOLL A4, whole road: 262,284,214 heavy-vehicle passages in the last 12 months, +1% on the year before
Notice: GPR 2025 average daily traffic on national roads outside city limits; per road, the sections measured inside the area, or else the nearest measured section (up to 15 km away).
```

## lookup_business

### A company by NIP

Call:

```json
{
  "tool": "lookup_business",
  "arguments": {
    "identifier": "5250000251"
  }
}
```

Structured content:

```json
{
  "name": "POLSKIE KOLEJE PAŃSTWOWE SPÓŁKA AKCYJNA",
  "legal_form": "SPÓŁKA AKCYJNA",
  "krs": "0000019193",
  "nip": "5250000251",
  "regon": "000126801",
  "vat_status": "Czynny",
  "seat": {
    "wojewodztwo": "Mazowieckie",
    "powiat": "Warszawa",
    "gmina": "Warszawa",
    "miejscowosc": "Warszawa"
  },
  "address": "Aleje Jerozolimskie 142A, 02-305 Warszawa",
  "website": "WWW.PKP.PL",
  "registered_on": "11.06.2001",
  "last_entry_on": "08.10.2026",
  "share_capital_pln": 10150715600.0,
  "main_activity": "70.10.Z Działalność firm centralnych (head offices) i holdingów, z wyłączeniem holdingów finansowych",
  "other_activities": [
    "68.20.Z Wynajem i zarządzanie nieruchomościami własnymi lub dzierżawionymi",
    "68.10.Z Kupno i sprzedaż nieruchomości na własny rachunek",
    "52.21.Z Działalność usługowa wspomagająca transport lądowy",
    "41.10.Z Realizacja projektów budowlanych związanych ze wznoszeniem budynków",
    "... 5 more"
  ],
  "representation": "ZARZĄD SPÓŁKI - DO SKŁADANIA OŚWIADCZEŃ WOLI I PODPISYWANIA W IMIENIU SPÓŁKI WYMAGANE JEST WSPÓŁDZIAŁANIE PREZESA I CZŁONKA ZARZĄDU, DWÓCH CZŁONKÓW ZARZĄDU ALBO JEDNEGO CZŁONKA ZARZĄDU ŁĄCZNIE Z PROKURENTEM. JEŻELI ZARZĄD JEST JEDNOOSOOWY, DO SKŁADANIA OŚWIADCZEŃ WOLI I PODPISYWANIA W IMIENIU SPÓŁKI UPOWAŻNIONY JEST JEDEN CZŁONEK ZARZĄDU. PREZES ZARZĄDU SPÓŁKI, W ZAKRESIE REALIZACJI ZADAŃ OBRONNYCH W SPÓŁCE WYNIKAJĄCYCH Z PRZEPISÓW O POWSZECHNYM OBOWIĄZKU OBRONY ORAZ KOORDYNACJI ZADAŃ OBRONNYCH REALIZOWANYCH W SPÓŁKACH UTWORZONYCH PRZEZ PKP S.A. NA PODSTAWIE ART. 14, 15 I 19 USTAWY Z DNIA 8 WRZEŚNIA 2000 R. O KOMERCJALIZACJI, RESTRUKTURYZACJI I PRYWATYZACJI PRZEDSIĘBIORSTWA PAŃSTWOWEGO POLSKIE KOLEJE PAŃSTWOWE (DZ. U. 2000 NR 84 POZ. 948 Z PÓŹN. ZM), UPRAWNIONY JEST DO JEDNOOSOBOWEGO SKŁADANIA OŚWIADCZEŃ WOLI I PODPISYWANIA W IMIENIU SPÓŁKI.",
  "board_roles": [
    "PREZES ZARZĄDU",
    "CZŁONEK ZARZĄDU",
    "WICEPREZES ZARZĄDU",
    "CZŁONEK ZARZĄDU"
  ],
  "owners": [
    "SKARB PAŃSTWA"
  ],
  "warnings": [],
  "sources": [
    "Wykaz podatników VAT (Ministry of Finance)",
    "Krajowy Rejestr Sądowy (Ministry of Justice), current extract"
  ],
  "retrieved_at": "2026-10-10T19:08:16.926590Z"
}
```

Text:

```
POLSKIE KOLEJE PAŃSTWOWE SPÓŁKA AKCYJNA (SPÓŁKA AKCYJNA)
KRS 0000019193, NIP 5250000251, REGON 000126801; VAT: Czynny
Address: Aleje Jerozolimskie 142A, 02-305 Warszawa
Share capital: 10,150,715,600 PLN
Main activity: 70.10.Z Działalność firm centralnych (head offices) i holdingów, z wyłączeniem holdingów finansowych
Board: PREZES ZARZĄDU, CZŁONEK ZARZĄDU, WICEPREZES ZARZĄDU, CZŁONEK ZARZĄDU (names are masked in KRS)
Corporate owners: SKARB PAŃSTWA
Registered 11.06.2001; last entry 08.10.2026
Warnings: none in the register
Sources: Wykaz podatników VAT (Ministry of Finance); Krajowy Rejestr Sądowy (Ministry of Justice), current extract
```

## find_businesses

### Plumbers and heating firms in Smolec

Call:

```json
{
  "tool": "find_businesses",
  "arguments": {
    "town": "Smolec",
    "pkd": "43.22.Z"
  }
}
```

Structured content:

```json
{
  "count": 56,
  "listed": [
    {
      "name": "<name>",
      "nip": "<nip>",
      "regon": "<regon>",
      "status": "AKTYWNY",
      "started": "2026-01-15",
      "address": "<address>",
      "gmina": "Kąty Wrocławskie",
      "terc": "0223045"
    },
    {
      "name": "<name>",
      "nip": "<nip>",
      "regon": "<regon>",
      "status": "AKTYWNY",
      "started": "2024-11-12",
      "address": "<address>",
      "gmina": "Kąty Wrocławskie",
      "terc": "0223045"
    },
    {
      "name": "<name>",
      "nip": "<nip>",
      "regon": "<regon>",
      "status": "AKTYWNY",
      "started": "2024-07-08",
      "address": "<address>",
      "gmina": "Kąty Wrocławskie",
      "terc": "0223045"
    },
    {
      "name": "<name>",
      "nip": "<nip>",
      "regon": "<regon>",
      "status": "AKTYWNY",
      "started": "2024-04-02",
      "address": "<address>",
      "gmina": "Kąty Wrocławskie",
      "terc": "0223045"
    },
    "... 1 more"
  ],
  "filters": {
    "miasto": "Smolec",
    "pkd": "4322Z",
    "status": "AKTYWNY"
  },
  "notices": [
    "Sole traders only (CEIDG); companies are in KRS and are not counted here.",
    "A PKD code matches any activity a trader declared, not only the main one."
  ],
  "source": "CEIDG (Ministry of Development), via dane.biznes.gov.pl",
  "retrieved_at": "2026-10-10T21:58:01.229170Z"
}
```

Text:

```
56 sole traders match (miasto Smolec, pkd 4322Z, status AKTYWNY).
- <name>, <address> (NIP <nip>, <status>, since <date>)
- <name>, <address> (NIP <nip>, <status>, since <date>)
- <name>, <address> (NIP <nip>, <status>, since <date>)
- <name>, <address> (NIP <nip>, <status>, since <date>)
- <name>, <address> (NIP <nip>, <status>, since <date>)
Notice: Sole traders only (CEIDG); companies are in KRS and are not counted here.
Notice: A PKD code matches any activity a trader declared, not only the main one.
```

## get_job

### An unknown job

Call:

```json
{
  "tool": "get_job",
  "arguments": {
    "job_id": "j_0000000000000000"
  }
}
```

Structured content:

```json
{
  "error": {
    "code": "unknown_job",
    "message": "No job 'j_0000000000000000' (jobs are kept for 7 days).",
    "hint": ""
  }
}
```

Text:

```
unknown_job: No job 'j_0000000000000000' (jobs are kept for 7 days).
```

## Errors

### Unknown variable, with suggestions

Call:

```json
{
  "tool": "get_timeseries",
  "arguments": {
    "area_id": "a_pt_51.1100_17.0320_sq1581",
    "variables": [
      "temperature"
    ],
    "start": "2024-01-01",
    "end": "2025-01-01"
  }
}
```

Structured content:

```json
{
  "error": {
    "code": "unknown_variable",
    "message": "No source provides 'temperature'.",
    "hint": "Did you mean climate_projection, t2m, t2m_max? Call list_data to see variables."
  }
}
```

Text:

```
unknown_variable: No source provides 'temperature'. Hint: Did you mean climate_projection, t2m, t2m_max? Call list_data to see variables.
```

### Area too large

Call:

```json
{
  "tool": "get_timeseries",
  "arguments": {
    "area_id": "a_pt_51.1100_17.0320_sq20000",
    "variables": [
      "ndvi"
    ],
    "start": "2024-01-01",
    "end": "2025-01-01"
  }
}
```

Structured content:

```json
{
  "error": {
    "code": "area_too_large",
    "message": "40.00 km square around 51.1100, 17.0320 is 1,600 km²; the limit is 1,000 km².",
    "hint": "Use a smaller area (for example a gmina instead of a powiat) or split it."
  }
}
```

Text:

```
area_too_large: 40.00 km square around 51.1100, 17.0320 is 1,600 km²; the limit is 1,000 km². Hint: Use a smaller area (for example a gmina instead of a powiat) or split it.
```
