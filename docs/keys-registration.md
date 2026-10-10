# Where to register for data keys

Checked against the providers' own pages on 9 October 2026.
"Not verified" means the provider does not state it publicly; ask them before relying on it.
Put every key in `.env` (mode 600), never in code or chat.

## Already set up

| Service | Unlocks | `.env` |
|---|---|---|
| Copernicus Data Space (Sentinel Hub OAuth client) | Sentinel-2, Sentinel-1, Sentinel-5P, Copernicus DEM | `GEOMCP_CDSE_CLIENT_ID`, `GEOMCP_CDSE_CLIENT_SECRET` |
| Climate Data Store (personal access token) | ERA5-Land weather and climate extremes | `GEOMCP_CDS_KEY` |

## Poland: do now

### GUS BDL key (recommended, 2 minutes)

The statistics tools (`find_statistics`, `get_statistics`, `get_summary` population facts) use the GUS Local Data Bank.
Anonymous access allows 1,000 requests per 12 hours for the whole server, which one busy day of searching can use up.

1. Open https://api.stat.gov.pl/Home/BdlApi and go to the "Rejestracja" tab.
2. Enter your email; the key is generated at once.
3. Add `GEOMCP_GUS_BDL_KEY=<key>` to `.env` and restart geomcp.

| | Anonymous | With key |
|---|---|---|
| Per second | 5 | 10 |
| Per 15 minutes | 100 | 500 |
| Per 12 hours | 1,000 | 5,000 |
| Per 7 days | 10,000 | 50,000 |

Free; data under CC BY 4.0, commercial use allowed with attribution.

### GUS SMUP key (optional)

Same limits and header as BDL, used for public-services indicators.
Register with "Zarejestruj" at https://api.stat.gov.pl/Home/SmupApi; the key arrives by email.
Add `GEOMCP_GUS_SMUP_KEY=<key>`.

## Poland: for the business wave (D)

### REGON (BIR) user key

Company details by NIP, REGON or KRS number.

1. Email regon_bir@stat.gov.pl asking for a BIR1.2 user key.
2. Include: company name and REGON, contact person and email, phone, the server's public IP addresses (sessions only work from the IP that opened them), and the expected number of concurrent users.
3. GUS replies with a 20-character key, usually after a manual review (timing not verified).

Free.
Put the VPS's IP in the request, since the server will make the calls.
Add `GEOMCP_REGON_KEY=<key>` and restart: `lookup_business` then also covers sole traders and non-VAT payers, with PKD codes.
REGON looks up NIP, REGON and KRS numbers only; no free register searches companies by name.

### CEIDG token (sole traders)

1. Log in at https://www.biznes.gov.pl with Profil Zaufany (login.gov.pl).
2. Submit the e-service request at https://www.biznes.gov.pl/pl/e-uslugi/00_9999_00 ("Hurtownia danych" permissions) and accept the data-processing statement.
3. After approval, a JWT token is issued for `https://dane.biznes.gov.pl/api/ceidg/v3/`.

Free; 1,000 requests per hour.
Add `GEOMCP_CEIDG_TOKEN=<token>` to `.env` and restart: `find_businesses` then searches and counts sole traders, and `lookup_business` adds their details.

### TERYT web service account (optional)

Only needed for street-level registers and boundary history.
Email teryt_ws1@stat.gov.pl with a user name, phone, email and type (commercial); GUS creates a username and password.

### No registration needed

KRS (company register), GUGiK (parcels, terrain, orthophoto, boundaries), GDOŚ, PIG-PIB, ISOK (Wody Polskie), IMGW and GIOŚ.
GUS TranStat works anonymously (100 requests per 15 minutes); its optional key uses the header `XApiKey` and is requested at https://api.stat.gov.pl/Home/TranStatApi.

## Europe (M5): register when the EU wave starts

| Service | Unlocks | Where | What you get | Cost and terms |
|---|---|---|---|---|
| EU Login | EGMS and Copernicus Land | https://ecas.ec.europa.eu/cas/eim/external/register.cgi | An account (email confirmation) | Free |
| Copernicus Land Monitoring (CLMS) | Ground motion (EGMS), high-resolution land layers, urban atlas | https://land.copernicus.eu, log in, profile, "API Tokens", "Create new Token" (shown once) | A JSON service key; geomcp exchanges it for 1-hour tokens | Free; commercial use allowed with attribution |
| Atmosphere Data Store (ADS) | CAMS air-quality reanalysis and forecasts | https://ads.atmosphere.copernicus.eu, same ECMWF account as the CDS; accept each dataset's licence on its download form | A personal access token | Free; licence per dataset |
| NASA Earthdata | Landsat archive, MODIS, VIIRS night lights | https://urs.earthdata.nasa.gov/users/new, then "Generate Token" | A bearer token valid 60 days | Free; commercial terms not verified |
| Google Earth Engine | Fallback compute and extra catalogues | https://code.earthengine.google.com/register with a Google Cloud project | OAuth or a service account | Free tiers are noncommercial only; commercial plans from $500 per month plus compute |
| OpenRouteService | Travel-time areas (isochrones), routing | https://account.heigit.org/signup | An API key (`Authorization` header) | Free tier about 2,000 directions per day (not verified); commercial use not verified |
| Open-Meteo | Weather forecasts and projections | No signup for the free API; paid plans at https://open-meteo.com/en/pricing | A paid `apikey` | Free tier is noncommercial only; paid plans for commercial use |

### No registration needed (Europe)

PVGIS solar potential (30 calls per second), ECMWF open forecasts (CC BY 4.0), Eurostat (including the census population grid), GHSL settlement and population layers, and the EEA datahub.

## Commercial use in short

Free for commercial use: GUS (with attribution), Copernicus (with attribution), ECMWF open data, KRS.
Noncommercial unless paid: Google Earth Engine, Open-Meteo.
Check before commercial use: NASA Earthdata products, OpenRouteService free tier.
geomcp flags restricted sources with `commercial_safe=false` in every answer.
