# Radiation widget

The radiation widget shows the **ambient gamma dose rate** in µSv/h near your location,
from open monitoring networks. A normal background is roughly 0.05–0.20 µSv/h, depending
on the ground, the altitude and the network. The widget is there to notice a real
change, not to measure your home.

The code is in `intercom_webtab/radiation/`. The research behind the choice of networks,
including the networks that were evaluated and rejected, is in
[notes/radiation-sources.md](notes/radiation-sources.md).

## Networks

| Id | Network | Coverage | What the value is | Licence |
|---|---|---|---|---|
| `bfs_odl` | BfS ODL-Info, about 1,700 probes | Germany | Official probe, 1-hour mean, cosmic part included (`dose`) | dl-de/by-2-0 |
| `stuk_fmi` | STUK via FMI open data, about 255 probes | Finland | Official probe, 10-minute mean (`dose`) | CC BY 4.0 |
| `epa_radnet` | US EPA RadNet, about 140 monitors, only some with a dose detector | United States | Official probe (`dose`) | Public domain |
| `hc_fps` | Health Canada Fixed Point Surveillance, about 80 stations | Canada | Terrestrial gamma only, cosmic part removed (`terrestrial`), so about 0.03 µSv/h lower than a `dose` probe | OGL-Canada |
| `safecast` | Safecast real-time sensors, about 150 active devices | Worldwide, mostly Japan, the US and Europe | Volunteer Geiger counters, counts converted with the tube's Cs-137 factor (`community`) | CC0 |

None of them needs a key or an account. The station checks for new data every 6 hours,
and retries after 30 minutes on failure. National networks update every 10 minutes to a
few hours.

## Automatic choice

With `[radiation] provider = auto` (the default):

1. Each national network has a simplified country outline (Natural Earth, public
   domain). Networks whose outline contains your point are tried first. Networks within
   50 km of their border come next, nearest first. Safecast always comes last, as the
   worldwide fallback.
2. The first network that returns **at least one fresh station within `radius_km`**
   wins. A network error, a timeout or an empty answer moves on to the next one. Each
   attempt gets 20 seconds.
3. If no network has a station in range, the widget says **"no stations nearby"**. If a
   network failed with an error, the status line says the radiation data did not load.

`provider = <id>` uses one network only. `provider = none` turns the widget off, so the
station makes no radiation requests and gives the slot to the first widget not already
on the list.

## Rules for every network

- Readings older than 3 days are dropped, and so are readings dated more than 1 hour in
  the future (device clocks).
- Values must be above 0 and below 0.1 Sv/h. High values are **never** dropped as
  outliers, because a real event must reach the panel.
- Duplicate stations are merged. Stations are sorted by great-circle distance, and at
  most 25 are kept.

## What the widget shows

- **Normal:** the value of the nearest station, with "normal, N stations".
- **Raised:** levels start at 0.20, 0.30, 0.60 and 1.0 µSv/h. One station alone can
  raise the dot only to yellow. Higher levels need confirmation: at least two stations
  at that level, or the same station at that level on two different days. The note says
  which ("2 of 8 stations", or "second day running"). Then the status line may warn,
  for example "Radiation above usual: 0.34 µSv/h", or "close windows" at the critical
  level.
- **Stale:** if the newest reading is older than 48 hours, or no download has succeeded
  for 18 hours, the widget shows the time of the reading instead of a fresh value.

## Limitations

- **Some places have no open real-time network within 100 km.** On 2026-10-09, London,
  Paris and Denver were examples (Denver's RadNet monitor has no dose detector). The
  widget then shows "no stations nearby". Raise `radius_km`, or set `provider = none`
  and use the slot for something else.
- **Near a border**, the simplified outlines may pick the neighbour's network first. Set
  `provider` explicitly if that matters.
- **Networks are not strictly comparable.** Canada reports terrestrial gamma only.
  Safecast values are converted counts. BfS changed its calculation in July 2025, and
  its values rose slightly.
- Times refer to the measurement, not the download. Official networks lag 10 minutes
  to 3 hours.

## Privacy

BfS, FMI and Health Canada receive a bounding box around your point, of `radius_km`.
Safecast returns the latest record of every device, and the distances are computed
locally. EPA RadNet files are downloaded per station from a built-in station list, so
EPA receives no coordinates.

## Adding a network

A network qualifies only if it has a documented, publicly reachable feed (no session
cookies, keys or accounts), and its terms allow a free open-source client to fetch and
display the values. Attribution requirements are fine.

1. Create `intercom_webtab/radiation/<id>.py` with `ID`, `NAME`, `SOURCE_URL`,
   `LICENSE`, `LICENSE_URL`, `ATTRIBUTION` and `KIND`. Add `COVERAGE` as rings parsed with
   `base.parse_rings`, or `None` for worldwide.
2. Implement `fetch(lat, lon, radius_km, timeout)`. It returns `base.station(...)`
   records within the radius, and it gets all data through `base.http_get` or
   `base.get_json`. These enforce the time budget, the size limit and the User-Agent, and
   the tests replace them to run offline.
3. Add the module to `PROVIDERS` in `radiation/__init__.py`.
4. Add a trimmed real response as `tests/fx/rad_<id>.json` and tests in
   `tests/test_radiation.py`.
5. Add the network's licence and attribution to
   [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and the table above.
