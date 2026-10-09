# Radiation sources: research notes

Research for the radiation widget, checked on 2026-10-09. The station shows the ambient
gamma dose rate (uSv/h) near the configured location. The code is in
`intercom_webtab/radiation/`. These notes are raw material for the user documentation.

A network is included only if it passes both checks:

1. It has a documented, publicly reachable feed: an API, an open-data service, or a
   download link that the operator publishes. Session cookies, keys or accounts are not
   acceptable.
2. Its terms allow a free open-source client to fetch the values and display them.
   An attribution requirement is fine.

## How the network is chosen

`[radiation] provider = auto` (the default) works as follows:

1. Each national provider has a simplified country outline (Natural Earth, public domain,
   simplified to 20-30 km). A provider whose outline contains the point goes first.
   Providers whose outline lies within 50 km of the point come next, nearest first.
   Safecast always comes last as the worldwide fallback.
2. The first provider that returns at least one fresh station inside `radius_km` wins.
   A network error, a timeout or an empty answer moves on to the next provider.
   Each attempt gets its own `timeout` (20 s from the web server).
3. If nothing is found, the call raises `NoStationsError`, for example "no fresh stations
   within 100 km". If some providers failed with errors, it raises `RadiationError`.

`provider = <id>` uses one network only. `provider = none` switches the widget's data off.
Provider ids: `bfs_odl`, `stuk_fmi`, `epa_radnet`, `hc_fps`, `safecast`.

Rules applied to every provider:

- Readings older than 3 days are dropped.
- Readings dated more than 1 h in the future are dropped (device clock errors).
- Values must be greater than 0 and below 0.1 Sv/h.
- Duplicate stations are merged.
- Stations are sorted by great-circle distance. At most 25 are returned.

High values are never dropped as outliers, because a real event must reach the panel.
The panel logic decides how much to trust a single station.

Result shape (from `radiation.fetch`):

```
{"source", "source_url", "license", "license_url", "attribution", "provider",
 "stations": [{"name", "lat", "lon", "km", "usvh", "ts", "kind"}]}
```

`kind` says what the number means:

| kind | meaning |
|---|---|
| `dose` | Official probe. Ambient dose equivalent rate H*(10), cosmic part included. |
| `terrestrial` | Official spectrometric probe with the cosmic part removed. Canada only. Reads about 0.03 uSv/h lower than a `dose` probe at the same spot. |
| `community` | Volunteer Geiger counter. Counts are converted with the tube's calibration factor. |

Recommended refresh interval: 1-6 hours. The web server uses 6 h, with a retry after
30 min. National networks update every 10 min to a few hours.

## Implemented

### Germany: BfS ODL-Info (`bfs_odl`)

| | |
|---|---|
| Operator | Bundesamt fuer Strahlenschutz (BfS). About 1,700 probes. |
| Endpoint | `https://www.imis.bfs.de/ogc/opendata/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=opendata:odlinfo_odl_1h_latest&outputFormat=application/json&bbox=W,S,E,N` (WFS 1.0.0 uses the lon/lat axis order). |
| Docs | https://odlinfo.bfs.de/ODL/EN/service/data-interface/data-interface_node.html |
| Auth / limits | No key. No rate limit is published. Data change hourly. |
| Format / units | GeoJSON. `value` is in uSv/h, the latest 1-hour mean of the gross gamma dose rate. Also available: `value_cosmic`, `value_terrestrial`, `end_measure` (ISO UTC), `site_status` (1 = in operation, 2 = defective, 3 = test operation). |
| Filters | Only `site_status == 1` with a non-null value. |
| Licence | Datenlizenz Deutschland - Namensnennung - Version 2.0, https://www.govdata.de/dl-de/by-2-0 (site policy: https://www.imis.bfs.de/geoportal/resources/sitepolicy.html). BfS asks that data be shown "in an objective manner". |
| Attribution (UI) | `Radiation: BfS ODL-Info (dl-de/by-2-0)` |
| Note | From 1 July 2025 BfS changed how the dose rate is calculated, and values rose slightly. |

### Finland: STUK via FMI open data (`stuk_fmi`)

| | |
|---|---|
| Operator | Radiation and Nuclear Safety Authority (STUK). About 255 probes. Served by the Finnish Meteorological Institute (FMI). |
| Endpoint | `https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=getFeature&storedquery_id=stuk::observations::external-radiation::latest::multipointcoverage&bbox=W,S,E,N` |
| Auth / limits | No key. FMI asks for moderate request rates. |
| Format / units | GML MultiPointCoverage. Parameter `DR_PT10M_avg` is the 10-minute mean dose rate in uSv/h. `DRS1_PT10M_avg` is its relative uncertainty and is not used. Positions are listed as "lat lon epoch". |
| Filters | NaN values are skipped. Documents with a DOCTYPE or ENTITY declaration are refused. |
| Licence | CC BY 4.0. The FMI open data licence covers STUK's open data sets: https://en.ilmatieteenlaitos.fi/open-data-licence |
| Attribution (UI) | `Radiation: STUK via FMI open data (CC BY 4.0)` |

### United States: EPA RadNet (`epa_radnet`)

| | |
|---|---|
| Operator | US Environmental Protection Agency. About 140 fixed near-real-time air monitors. |
| Endpoint | `https://radnet.epa.gov/cdx-radnet-rest/api/rest/csv/<YEAR>/fixed/<STATE>/<CITY>`: one year-to-date CSV per station, linked from https://www.epa.gov/radnet/radnet-csv-file-downloads |
| Auth / limits | No key. No rate limit is published. Files are 0.3-0.8 MB, with no gzip and no range support. Data are updated several times a day. |
| Format / units | CSV columns `LOCATION_NAME`, `SAMPLE COLLECTION TIME` (UTC, `MM/DD/YYYY HH:MM:SS`), `DOSE EQUIVALENT RATE (nSv/h)`, eight gamma count-rate channels (CPM) and `STATUS`. |
| Filters | The newest row with a dose value is used. Rows whose STATUS contains REJECT or INVALID are skipped. |
| Licence | Public domain: works of the US Government, 17 U.S.C. 105. See https://www.usa.gov/government-copyright |
| Attribution (UI) | `Radiation: US EPA RadNet` |

Notes on RadNet:

- Only stations with an exposure-rate detector fill the dose column. On 2026-10-09,
  Boston, Worcester, Providence, Portsmouth, Washington, Austin and Colorado Springs had
  it. Denver, Las Vegas and Concord NH did not.
- The gamma count-rate channels measure the air filter. EPA says they cannot be converted
  to dose rate, so they are never used.
- The CSV has no coordinates. The module carries the station list from EPA's download page
  with approximate city-centre coordinates (GeoNames via the Open-Meteo geocoder, rounded
  to 0.01 degree). Distances are therefore good to a few km.
- Up to 3 of the nearest stations are downloaded in parallel. Up to 5 are tried if some
  have no dose column. In the first days of January the previous year's file is also tried.

### Canada: Health Canada FPS (`hc_fps`)

| | |
|---|---|
| Operator | Health Canada, Fixed Point Surveillance network. About 80 stations, many of them in rings around nuclear plants. |
| Catalogue | https://open.canada.ca/data/en/dataset/f0d1c3a9-cf78-4b07-af55-9e8d4303449e |
| Endpoint | `https://maps-cartes.services.geo.ca/server_serveur/rest/services/HC/fpsn_en/MapServer/1/query?where=1=1&geometry=W,S,E,N&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=location_name,latest_value_nSvHr,time,dose_rate_type&outSR=4326&returnGeometry=true&f=json` |
| Auth / limits | No key. Readings come about every 15 min. |
| Format / units | ArcGIS JSON. `latest_value_nSvHr` is an integer in nSv/h. `time` is in epoch ms (UTC). `dose_rate_type` is "Terrestrial Gamma". |
| Licence | Open Government Licence - Canada, https://open.canada.ca/en/open-government-licence-canada |
| Attribution (UI) | `Radiation: Health Canada FPS (OGL-Canada)`. The about page should carry the full statement "Contains information licensed under the Open Government Licence - Canada." |
| Note | The probes are NaI spectrometers and report terrestrial gamma only (kind `terrestrial`). Station names are codes such as `7000_8_45.5049_-73.5749`, shown as `FPS 7000-8`. |

### Worldwide fallback: Safecast real-time sensors (`safecast`)

| | |
|---|---|
| Operator | Safecast, a volunteer network. About 150 fixed devices report within any 3 days, mostly in Japan, the US and Europe. |
| Endpoint | `https://tt.safecast.org/devices?class=rad\|geigie\|pointcast\|safecast&template={...}` returns the latest record of every device, about 110 kB. Without `class` it is about 1.4 MB. Safecast's own published tools use the same query (`Safecast/realtime-grafana-last-24-hours` on GitHub). |
| Auth / limits | No key. No limit is published. Answers take 5-10 s. |
| Format / units | JSON list. `when_captured` (ISO UTC), `loc_lat`, `loc_lon`, `loc_name`, and tube count rates in CPM: `lnd_7318u`, `lnd_7318c`, `lnd_7128ec`. |
| Conversion | LND 7317/7318 pancake tubes (`lnd_7318u`, `lnd_7318c`): 334 CPM per uSv/h. LND 7128 EC (`lnd_7128ec`): 108 CPM per uSv/h. These are the factors Safecast's ingest server (TTServe) uses, based on a Cs-137 calibration. The pancake tube is preferred when a device has several. `lnd_712u` is ignored because its unit in the feed is ambiguous. `lnd_78017w` is ignored because it has no published factor. |
| Filters | Records below 1 CPM, at 0,0, with an unparseable or future date (the feed contains years such as 2044 and 2080) are dropped. Of duplicate records the newest is kept. |
| Licence | CC0 1.0, https://safecast.org/data/. Attribution is requested, not required. |
| Attribution (UI) | `Radiation: Safecast (CC0)` |
| Note | The documented `api.safecast.org/measurements.json` mostly holds mobile bGeigie drives. Spatial queries took over 40 s and ended in HTTP 500, so it is not used. |

## Evaluated and not included

| Network | Why not | Reference |
|---|---|---|
| EURDEP / JRC REMon (EU-wide) | No public API. The public map comes with a disclaimer only and no reuse licence. The data belong to the national contact points under administrative arrangements, and the full platform needs EU Login. | https://remap.jrc.ec.europa.eu/Help/Simple.aspx, https://remon.jrc.ec.europa.eu/About/Rad-Data-Exchange |
| Netherlands, RIVM NMR | The live map has been frozen since 28 April 2025 (the system is being rebuilt), and RIVM points users to EURDEP. Its INSPIRE WFS (`data.rivm.nl/geo/inspire/wfs`, layer `dosis_tempo_2011`, CC0) holds annual means only. Revisit when RIVM restores live data. | https://www.rivm.nl/nationaal-meetnet-radioactiviteit/resultaten |
| Switzerland, NADAM (NEOC / NAZ) | Only HTML tables, updated twice a day. No open-data licence. MeteoSwiss Open Government Data has no dose-rate set. The Swiss geoportal layer `ch.bag.radioaktivitaet-atmosphaere` is aerosol activity, not dose rate. | https://naz.ch/en/aktuell/tagesmittelwerte |
| Austria, Strahlenfruehwarnsystem | A JavaScript map (`mb.strahlenschutz.gv.at`). No documented API or licence was found, and nothing is on data.gv.at. | https://www.bmluk.gv.at/themen/klima-und-umwelt/strahlenschutz/fruehwarnsystem/messwerte.html |
| France, Teleray (ASNR, formerly IRSN) | A JavaScript site (`teleray.asnr.fr`) with no documented API. The open data on data.gouv.fr (Licence Ouverte 2.0) are annual laboratory sample results, not real-time dose rate. | https://www.data.gouv.fr/datasets/donnees-de-la-surveillance-radiologique-de-lenvironnement-menee-par-lasnr-en-france |
| Japan, NRA RAMIS | A JavaScript application with no documented API. No reuse terms for automated access were found. Safecast covers Japan densely instead. | https://www.ramis.nra.go.jp/ |
| Ireland, EPA radmon API | It does have an open API (CC BY 4.0, Swagger at https://data.epa.ie/radmon/swagger/). But it is a validated archive of 9.4 million REM-format sample records with no filtering, not a near-real-time dose-rate feed. | https://data.epa.ie/?p=2302 |
| uRADMonitor | Community reports and the developer's notes say API access needs an account (user id + key); this was not verified first-hand. No public terms for third-party display were found. | https://www.uradmonitor.com/ |
| Russia, EGASMRO (egasmro.ru) | No documented API and no published reuse terms. Not included. | https://egasmro.ru/ |
| United Kingdom (RIMNET) | No public real-time feed. In the UK only Safecast data are available, and they are sparse: the nearest real-time sensor to Greenwich was about 350 km away. | - |
| South Korea (iERNet via data.go.kr) | Needs a personal service key. | - |

## Known limitations (for the docs)

- Some places have no open real-time network within 100 km. Examples on 2026-10-09:
  London, Paris and Denver (whose RadNet probe has no dose detector). The widget then
  shows "no data". Users can raise `radius_km` or set `provider = none`.
- Within about 30 km of a national border, the simplified outlines may pick the
  neighbour's network first. Detroit and Windsor are an example. Set `provider`
  explicitly if that matters.
- Values from different networks are not strictly comparable:
  - Canada reports terrestrial gamma only.
  - Safecast values are counts converted with a Cs-137 factor.
  - BfS raised its values slightly in July 2025.
- `ts` is the measurement time. Official networks lag 10 min to 3 h.

## Adding a provider

Create a module in `intercom_webtab/radiation/`. It needs:

- `ID`, `NAME`, `SOURCE_URL`, `LICENSE`, `LICENSE_URL`, `ATTRIBUTION` and `KIND`.
- `COVERAGE`: rings parsed with `base.parse_rings`, or `None` for worldwide.
- `fetch(lat, lon, radius_km, timeout)`: it returns `base.station(...)` records inside
  the radius and gets all its data through `base.http_get` / `base.get_json`.

Then add the module to `PROVIDERS` in `radiation/__init__.py`, and add a fixture plus a
test in `tests/test_radiation.py`.
