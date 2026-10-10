"""Guide §10 gotchas for sources not implemented yet: each becomes a real test in its phase."""

import pytest

LATER = {
    "03_median_needs_single_time_chunk": "own pixel engine (phase 3)",
    "06_arco_era5_longitudes_0_360": "ARCO ERA5 is not used; Earthmover fallback planned",
    "09_planetary_computer_sas_expiry": "Planetary Computer adapters (phase 3)",
    "10_requester_pays_buckets": "own pixel engine (phase 3)",
    "12_landsat_7_slc_off": "Landsat adapter (phase 3)",
    "14_modis_ending": "no MODIS source planned",
    "15_imerg_v07_v08": "IMERG adapter (phase 3)",
    "16_cams_forecast_retention": "CAMS adapter (deferred to M3)",
    "19_xee_breaking_api": "GEE sidecar (phase 2)",
    "20_overture_schema_v2": "Overture is not planned; OSM via Overpass (phase 2)",
}


@pytest.mark.parametrize("gotcha", sorted(LATER))
def test_gotcha_later_phase(gotcha: str) -> None:
    pytest.xfail(f"gotcha {gotcha}: becomes a real test with the {LATER[gotcha]}")
