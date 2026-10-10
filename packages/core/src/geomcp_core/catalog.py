"""The human-readable data catalogue behind `describe_data`.

One entry per source (adapter id) or dataset (statistics); a test checks every registered adapter has one.
Numbers come from live measurements recorded in docs/adr.
"""

from pydantic import BaseModel, Field


class CatalogEntry(BaseModel):
    id: str
    category: str
    measures: str = Field(description="What it tells you, in plain language")
    region: str = Field(description="Where it works: worldwide, Europe or Poland")
    history: str = Field(description="How far back the data goes")
    update_frequency: str
    tools: list[str] = Field(description="Tools that use it")
    limits: str = ""
    cost: str = "free"
    examples: list[str] = Field(description="Example questions, Polish and English")
    caveats: list[str] = Field(default_factory=list)


CATEGORIES = [
    "Hazards and constraints",
    "Energy",
    "Satellite imagery and vegetation",
    "Climate and weather",
    "Air quality",
    "Terrain",
    "Land, buildings and infrastructure",
    "Population, economy and public services",
]

ENTRIES: dict[str, CatalogEntry] = {
    e.id: e
    for e in [
        CatalogEntry(
            id="sentinel2",
            category="Satellite imagery and vegetation",
            measures="Vegetation greenness (NDVI), moisture (NDMI), water (NDWI, MNDWI), built-up (NDBI) and burn "
            "scars (NBR), from 10 m optical satellite images every 2-5 days",
            region="worldwide",
            history="2016 onwards",
            update_frequency="every 2-5 days (a few days per month are cloud-free)",
            tools=["get_timeseries", "compare_areas", "get_summary", "get_snapshot"],
            limits="areas up to 1,000 km²",
            cost="about 290 Sentinel Hub units for 10 years of one area (10,000 free per month); repeats are free",
            examples=[
                "Jak zmieniała się roślinność w gminie X od 2016 roku?",
                "Which of these three gminas was greenest in summer 2025?",
            ],
            caveats=["Clouds hide the ground: periods without a clear view are reported as gaps"],
        ),
        CatalogEntry(
            id="sentinel1",
            category="Satellite imagery and vegetation",
            measures="Radar backscatter (VV, VH in dB): surface roughness, soil moisture, structures; "
            "works through clouds",
            region="worldwide",
            history="2016 onwards",
            update_frequency="every 6-12 days",
            tools=["get_timeseries", "compare_areas", "get_snapshot"],
            limits="areas up to 1,000 km²",
            cost="small Sentinel Hub units",
            examples=["Czy radar pokazuje zmiany na terenie budowy?", "Show the radar image of this area"],
            caveats=["Ascending orbits only, so values are comparable over time"],
        ),
        CatalogEntry(
            id="copernicus_dem",
            category="Terrain",
            measures="Height and slope of the surface (including buildings and trees)",
            region="worldwide",
            history="static (2011-2015 radar survey)",
            update_frequency="static",
            tools=["get_summary", "get_snapshot"],
            examples=["Jak wysoko leży ta gmina?", "How steep is this area?"],
            caveats=["A 90 m surface model: towns and forests look higher and steeper than the ground"],
        ),
        CatalogEntry(
            id="gugik_nmt",
            category="Terrain",
            measures="Bare-ground height and slope from Poland's official 1 m terrain model",
            region="Poland",
            history="current national survey",
            update_frequency="updated by region every few years",
            tools=["get_summary"],
            limits="areas up to 250 km²; 1 m cells for small areas, coarser for gminas",
            examples=["Jaki jest spadek terenu na tej działce?", "What is the ground elevation of this parcel?"],
            caveats=["Heights in the Polish Kronstadt 86 datum; the service is slow at times"],
        ),
        CatalogEntry(
            id="era5_land",
            category="Climate and weather",
            measures="Air temperature (mean, min, max), precipitation, wind, solar radiation, soil moisture and "
            "vapour-pressure deficit, from a global reanalysis of weather observations; climate extremes as day "
            "counts: hot days, summer days, tropical nights, frost days, heavy-rain days, dry days",
            region="worldwide",
            history="1950 onwards",
            update_frequency="daily, about 6 days behind; the last 3 months are preliminary",
            tools=["get_timeseries", "compare_areas", "get_summary"],
            cost="free (Copernicus Climate Data Store)",
            examples=[
                "Czy w gminie X robi się cieplej? O ile na dekadę?",
                "How much did it rain here in each of the last 10 years?",
                "Ile było upalnych dni i nocy tropikalnych rocznie od 2000 roku?",
            ],
            caveats=["One 9 km grid cell at the area's centre: local differences are smoothed"],
        ),
        CatalogEntry(
            id="sentinel5p",
            category="Air quality",
            measures="Nitrogen dioxide (NO₂) in the air column, a marker of traffic and industry, seen from space",
            region="worldwide",
            history="May 2018 onwards",
            update_frequency="daily, about 5 days behind",
            tools=["get_timeseries", "compare_areas", "get_summary"],
            examples=["Jak zmieniało się zanieczyszczenie NO₂ nad Wrocławiem?", "Is NO2 lower than five years ago?"],
            caveats=["Pixels of 5.5 x 3.5 km: small areas share a pixel with their surroundings"],
        ),
        CatalogEntry(
            id="gios",
            category="Air quality",
            measures="PM10, PM2.5, NO₂ and ozone measured at official monitoring stations: last 24 hours, and "
            "annual means per year since 2000",
            region="Poland",
            history="current conditions; annual means 2000 onwards (varies by station)",
            update_frequency="hourly",
            tools=["get_summary"],
            examples=[
                "Jaka jest teraz jakość powietrza w centrum Wrocławia?",
                "Jak zmieniało się stężenie PM10 i PM2,5 w ostatnich 15 latach?",
            ],
            caveats=[
                "Nearest station within 25 km; current values are operational data, not yet verified by GIOŚ",
                "History: verified annual means (pm10_history, pm25_history, no2_history), up to last year",
            ],
        ),
        CatalogEntry(
            id="worldcover",
            category="Land, buildings and infrastructure",
            measures="Shares of tree cover, cropland, grassland, built-up land, water and other classes",
            region="worldwide",
            history="2021 (single snapshot)",
            update_frequency="static",
            tools=["get_summary"],
            examples=["Jaka część gminy to lasy, a jaka pola?", "How built-up is this area?"],
        ),
        CatalogEntry(
            id="bdot10k",
            category="Land, buildings and infrastructure",
            measures="Buildings by function (residential, offices, retail and services, industrial, storage, "
            "agricultural, education, healthcare, ...), total footprint and mean floors",
            region="Poland",
            history="current national topographic database",
            update_frequency="every 1-2 years per powiat",
            tools=["get_summary"],
            limits="areas up to 250 km²; the first area in a powiat takes about 30 s",
            examples=["Ile jest budynków usługowych i przemysłowych w gminie?", "What share of buildings is housing?"],
        ),
        CatalogEntry(
            id="osm",
            category="Land, buildings and infrastructure",
            measures="Counts of buildings, schools, healthcare places, shops and public-transport stops, "
            "and road length",
            region="worldwide",
            history="current map",
            update_frequency="continuous (volunteer mapping)",
            tools=["get_summary"],
            limits="areas up to 250 km²",
            examples=["Ile budynków i szkół jest w gminie X?", "How many public-transport stops are in this area?"],
            caveats=["Completeness depends on volunteer mapping; usually good in Polish towns"],
        ),
        CatalogEntry(
            id="gus_bdl",
            category="Population, economy and public services",
            measures="Headline gmina statistics: population, density, migration, natural increase, new dwellings, "
            "new businesses and budget income per resident",
            region="Poland",
            history="1995 onwards (varies by indicator)",
            update_frequency="yearly",
            tools=["get_summary"],
            examples=["Ilu mieszkańców ma gmina X i jak to się zmienia?", "Is this gmina growing?"],
            caveats=["Figures are for the whole gmina containing the area"],
        ),
        CatalogEntry(
            id="gus_statistics",
            category="Population, economy and public services",
            measures="Thousands of official indicators from the GUS Local Data Bank: age structure, wages, "
            "unemployment, education, health, housing, environment, tourism, businesses by sector, budgets",
            region="Poland",
            history="1995 onwards (varies by indicator)",
            update_frequency="yearly",
            tools=["find_statistics", "get_statistics"],
            examples=[
                "Jakie jest przeciętne wynagrodzenie w powiecie wrocławskim w porównaniu z województwem?",
                "How many businesses per sector are registered in this gmina?",
            ],
            caveats=["Some indicators exist only for powiats, voivodeships or Poland; the tool says which level"],
        ),
        CatalogEntry(
            id="isok_flood",
            category="Hazards and constraints",
            measures="Share of the area in official flood hazard zones: once in 10, 100 and 500 years, and if a "
            "flood embankment fails",
            region="Poland",
            history="current official maps (2020 cycle)",
            update_frequency="every 6 years",
            tools=["get_constraints"],
            limits="areas up to 250 km²",
            examples=["Czy ta działka leży w strefie zagrożenia powodzią?", "How much of this gmina is flood-prone?"],
            caveats=["Zones include the river bed itself; land behind embankments counts only in the failure scenario"],
        ),
        CatalogEntry(
            id="gdos_protected",
            category="Hazards and constraints",
            measures="Nature protection: Natura 2000 sites, national and landscape parks, reserves, protected "
            "landscape areas, ecological use areas, with names and shares of the area",
            region="Poland",
            history="current register",
            update_frequency="continuous",
            tools=["get_constraints"],
            examples=["Czy na tym terenie są obszary Natura 2000?", "Which protected areas overlap this site?"],
        ),
        CatalogEntry(
            id="pig_geohazards",
            category="Hazards and constraints",
            measures="Landslides and landslide-prone areas, mining terrains and mining areas, mineral deposits, and "
            "vertical ground motion (subsidence) in mm per year",
            region="Poland",
            history="current registers; ground motion 2016-2020 (EGMS)",
            update_frequency="registers continuous; ground motion yearly releases",
            tools=["get_constraints"],
            examples=["Czy grunt w tej okolicy osiada przez kopalnie?", "Are there landslides or mining areas here?"],
            caveats=[
                "Ground motion is interpolated between radar points; an indication, not a survey",
                "Landslides are those mapped by SOPO (mostly the Carpathians); unmapped regions show none",
            ],
        ),
        CatalogEntry(
            id="gios_noise",
            category="Hazards and constraints",
            measures="Road, rail, aircraft and industrial noise: share of the area at Lden 55 dB or more "
            "(annoyance) and 65 dB or more (health risk)",
            region="Poland (major roads, railways and airports, and cities over 100,000 people)",
            history="strategic noise maps 2017 (round 3)",
            update_frequency="every 5 years",
            tools=["get_constraints"],
            examples=["Czy ta działka jest w zasięgu hałasu z lotniska?", "How noisy is this street from traffic?"],
            caveats=["Modelled maps from 2017; newer 2022 maps are not available as a queryable service"],
        ),
        CatalogEntry(
            id="iung_drought",
            category="Hazards and constraints",
            measures="Agricultural drought risk this season for the gmina: which crops were at risk in which "
            "10-day periods, per soil category, with the soil category under the area",
            region="Poland",
            history="current season (2020 onwards on the IUNG site)",
            update_frequency="every 10 days, March to September",
            tools=["get_constraints"],
            examples=["Czy w tej gminie jest susza rolnicza?", "Which crops are at drought risk here this year?"],
        ),
        CatalogEntry(
            id="imgw_warnings",
            category="Hazards and constraints",
            measures="Current IMGW weather warnings for the powiat and hydrological warnings (floods, drought) "
            "for the voivodeship",
            region="Poland",
            history="current only",
            update_frequency="continuous",
            tools=["get_constraints"],
            examples=["Czy są teraz ostrzeżenia pogodowe dla tej gminy?", "Is there a drought warning here?"],
        ),
        CatalogEntry(
            id="gugik_orthophoto",
            category="Satellite imagery and vegetation",
            measures="Aerial photo of the area, up to 25 cm per pixel: buildings, roofs, plots, roads in detail",
            region="Poland",
            history="most recent national flight (year varies by region)",
            update_frequency="every 1-3 years per region",
            tools=["get_snapshot"],
            examples=["Pokaż zdjęcie lotnicze tej działki", "Show an aerial photo of this site"],
        ),
        CatalogEntry(
            id="rcn_prices",
            category="Land, buildings and infrastructure",
            measures="Real transaction prices from notarial deeds: apartments (PLN/m², new and resale), houses "
            "and undeveloped plots (PLN/m²), yearly medians and counts",
            region="Poland (about 362 of 380 powiats publish)",
            history="mostly 2021 onwards (varies by powiat)",
            update_frequency="continuous (powiat registers)",
            tools=["get_prices"],
            limits="areas up to 25 km²",
            examples=[
                "Ile kosztuje metr mieszkania w Smolcu i jak to się zmienia?",
                "What do building plots sell for around this site?",
            ],
            caveats=[
                "Only single-property free-market sales are priced; counts per year depend on how fully each "
                "powiat publishes"
            ],
        ),
        CatalogEntry(
            id="kimpzp_zoning",
            category="Land, buildings and infrastructure",
            measures="Local zoning plan (MPZP) designations, e.g. housing, services, farmland with or without a "
            "building ban, with plan name, adoption date and link; for a parcel also its cadastre land-use class",
            region="Poland (gminas that publish to the national integration)",
            history="plans in force",
            update_frequency="continuous",
            tools=["get_zoning"],
            limits="areas up to 5 km², sampled at up to 16 points",
            examples=["Co można zbudować na tej działce według MPZP?", "Is this site zoned for housing?"],
            caveats=[
                "Some gminas, including Wrocław, do not publish to the national service",
                "Point sampling: read the plan text before any decision",
            ],
        ),
        CatalogEntry(
            id="gddkia_traffic",
            category="Land, buildings and infrastructure",
            measures="Vehicles per day in 2025, change since 2020/21 and heavy-vehicle share on national roads "
            "crossing the area, per measured section; for tolled roads also the 12-month heavy-vehicle trend "
            "from e-TOLL",
            region="Poland (national roads outside cities)",
            history="General Traffic Surveys 2020/21 and 2025",
            update_frequency="every 5 years",
            tools=["get_traffic"],
            examples=["Jaki jest ruch na drodze krajowej przy tej działce?", "How busy is the A4 near this gmina?"],
            caveats=["National roads outside city limits; the nearest measured section may lie a few km away"],
        ),
        CatalogEntry(
            id="krs_business",
            category="Population, economy and public services",
            measures="A company by KRS, NIP or REGON: name, legal form, VAT status, address, share capital, main "
            "and other activities (PKD), representation, board roles, corporate owners, liquidation or "
            "bankruptcy entries",
            region="Poland",
            history="current register extract",
            update_frequency="daily",
            tools=["lookup_business"],
            examples=["Sprawdź firmę o NIP 5250000251", "Who owns the company with KRS 0000019193?"],
            caveats=[
                "Companies are looked up by number only (no free register searches them by name)",
                "KRS masks the names of board members; sole traders come from CEIDG",
            ],
        ),
        CatalogEntry(
            id="ghsl_population",
            category="Population, economy and public services",
            measures="Residents living inside the area itself (not the whole gmina) in 2000, 2010, 2020 and 2025",
            region="worldwide",
            history="2000-2025 (2025 modelled)",
            update_frequency="every few years",
            tools=["get_summary"],
            examples=[
                "Ile osób mieszka w promieniu 2 km od tego punktu?",
                "How did the population of this district change since 2000?",
            ],
            caveats=["A 100 m model grid from censuses and satellite built-up maps, not a count of addresses"],
        ),
        CatalogEntry(
            id="pvgis",
            category="Energy",
            measures="Solar potential: yearly PV yield per kWp at the optimal tilt and flat, optimal tilt, and "
            "irradiation",
            region="Europe, Africa, most of Asia and the Americas",
            history="radiation 2005-2023",
            update_frequency="every few years",
            tools=["get_summary"],
            examples=["Ile energii da 10 kWp paneli na tym dachu?", "Is this site good for a solar farm?"],
        ),
        CatalogEntry(
            id="corine",
            category="Land, buildings and infrastructure",
            measures="Land-cover history: shares of urban fabric, industry and transport, construction, urban "
            "green, farmland, forest, wetlands and water in 1990, 2000, 2006, 2012 and 2018",
            region="Europe",
            history="1990-2018",
            update_frequency="every 6 years",
            tools=["get_summary"],
            examples=["Jak szybko zabudowywano pola wokół Smolca od 1990 roku?", "How much forest was lost here?"],
            caveats=["25 ha mapping unit: suits gminas and districts, not single plots"],
        ),
        CatalogEntry(
            id="climate_projection",
            category="Climate and weather",
            measures="Projected climate 2031-2050 against 1991-2010: daily maximum temperature, hot days per "
            "year and annual precipitation, with their changes",
            region="worldwide",
            history="projections to 2050",
            update_frequency="static",
            tools=["get_summary"],
            examples=["Ile upalnych dni będzie tu około 2040 roku?", "How much warmer will this gmina get by 2050?"],
            caveats=[
                "Non-commercial use only (Open-Meteo free API)",
                "Three global models downscaled to 10 km, no scenario choice; an indication, not a forecast",
            ],
        ),
        CatalogEntry(
            id="ceidg",
            category="Population, economy and public services",
            measures="Sole traders by name, gmina, town and PKD activity: how many are active and who they are "
            "(name, NIP, address, start date)",
            region="Poland",
            history="current register",
            update_frequency="daily",
            tools=["find_businesses", "lookup_business"],
            limits="CEIDG answers slowly (up to 90 s); 50 searches per 3 minutes",
            examples=[
                "Ile restauracji działa w gminie Kąty Wrocławskie?",
                "Find bakeries named 'piekarnia' in Wrocław",
            ],
            caveats=["Sole traders only: companies (sp. z o.o., S.A.) are in KRS"],
        ),
        CatalogEntry(
            id="smup",
            category="Population, economy and public services",
            measures="Quality and cost of local public services: education, social policy, culture, roads and "
            "transport, environment, real estate, construction permits, taxes",
            region="Poland",
            history="2010 onwards",
            update_frequency="yearly",
            tools=["find_statistics", "get_statistics"],
            examples=[
                "Jak gmina X wypada w dostępie do przedszkoli na tle powiatu?",
                "How long do construction permits take in this gmina?",
            ],
        ),
    ]
}

PLANNED: list[str] = [
    "Official boundaries (PRG) and TERYT registers",
    "Company details from GUS REGON (key requested)",
    "Europe: ground motion outside Poland (EGMS, needs an EU Login token)",
]
