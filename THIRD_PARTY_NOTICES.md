# Third-party notices

Intercom WebTab's own code is released under the [MIT License](LICENSE). This file lists
the parts that come from others or are under other terms, and the data and media that
the station fetches at run time. It also gives the credit each one requires.

## Bundled with the repository

### Phosphor Icons (MIT)

The button icons `house`, `lock` (lock-simple-open), `phone`, `refresh` (arrow-clockwise) and `x` in
`intercom_webtab/static/buttons/` come from [Phosphor Icons](https://phosphoricons.com/)
(`@phosphor-icons/core` 2.1.1, regular weight). The page embeds them as data URIs.

> Copyright (c) 2023 Phosphor Icons. MIT License.

The full licence text is in
[`intercom_webtab/static/buttons/LICENSE-phosphor.txt`](intercom_webtab/static/buttons/LICENSE-phosphor.txt).
The `gate` and `door` icons are original drawings of this project, under MIT.

### App icon (CC BY-SA 4.0)

`intercom_webtab/static/icon.svg` and the PNG renders made from it
(`intercom_webtab/static/icons/apple-touch-icon-*.png`):

> "Intercom WebTab icon" by Sasha Pavljenko ([pavljenko.ru](https://pavljenko.ru)), licensed under
> [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

The licence is also recorded in the SVG's `<metadata>`. Adaptations of the icon must be
shared under the same licence. This does not affect the MIT licence of the code.

### Test fixtures and mock panels

`tests/fx/` contains trimmed responses recorded from (or synthetic data shaped like) the
data services listed below:
Open-Meteo forecast and air quality, BfS, FMI/STUK, US EPA RadNet, Health Canada FPS and
Safecast. `intercom_webtab/static/mock/*.json` are panels computed from the Open-Meteo
recording. They are used under the same licences and credited the same way as the live
data.

## Data fetched at run time

The station's page shows a short credit line:
`Weather: Open-Meteo.com · Air: CAMS · Radiation: <network>`. The full credits follow.

### Open-Meteo (CC BY 4.0)

Weather forecasts and the air-quality data come from the
[Open-Meteo](https://open-meteo.com/) APIs.

> Weather data by [Open-Meteo.com](https://open-meteo.com/), licensed under
> [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

Open-Meteo combines models of national weather services. See
[open-meteo.com/en/license](https://open-meteo.com/en/license). The free API used here,
without a key, is for **non-commercial use** under Open-Meteo's
[terms](https://open-meteo.com/en/terms). For commercial use, you need an Open-Meteo
subscription. One station makes a few hundred requests a day, well within the free
limits.

### Copernicus Atmosphere Monitoring Service (CAMS)

Air-quality values (European AQI, PM2.5, PM10, ozone, NO₂, dust) are CAMS forecasts,
delivered by Open-Meteo.

> Contains modified Copernicus Atmosphere Monitoring Service information 2026.
> Neither the European Commission nor ECMWF is responsible for any use that may be made
> of the Copernicus information or data it contains.

The data are provided under the
[Licence to Use Copernicus Products](https://apps.ecmwf.int/datasets/licences/copernicus/).

### Radiation networks

Each provider's attribution and licence are also available in code, as
`radiation.describe(<id>)`.

| Network | Credit | Licence |
|---|---|---|
| Germany: BfS ODL-Info (`bfs_odl`) | Source: Bundesamt für Strahlenschutz (BfS), ODL-Info. Only the latest values near the configured point are shown. | [Datenlizenz Deutschland – Namensnennung – Version 2.0](https://www.govdata.de/dl-de/by-2-0) (dl-de/by-2-0). BfS asks that data be shown objectively. |
| Finland: STUK via FMI (`stuk_fmi`) | Radiation and Nuclear Safety Authority (STUK), distributed by the Finnish Meteorological Institute open data service | [CC BY 4.0](https://en.ilmatieteenlaitos.fi/open-data-licence) |
| United States: EPA RadNet (`epa_radnet`) | US Environmental Protection Agency, RadNet | Public domain: a work of the US Government (17 U.S.C. 105) |
| Canada: Health Canada FPS (`hc_fps`) | Contains information licensed under the Open Government Licence – Canada. | [OGL-Canada](https://open.canada.ca/en/open-government-licence-canada) |
| Worldwide: Safecast (`safecast`) | Radiation data: Safecast | [CC0 1.0](https://safecast.org/data/). Attribution is requested, not required. |

The approximate coordinates of the RadNet monitors in `radiation/epa_radnet.py` come
from [GeoNames](https://www.geonames.org/) (CC BY 4.0), through the Open-Meteo
geocoding API, rounded to 0.01°.

The simplified country outlines that decide which network covers a point are derived
from [Natural Earth](https://www.naturalearthdata.com/), which is in the public domain.

### Background photos (CC0 / Public Domain Mark)

The weather photos are listed in
[`intercom_webtab/weather/photo_ids.json`](intercom_webtab/weather/photo_ids.json): 91
photos, 71 under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) and 20
marked with the [Public Domain Mark 1.0](https://creativecommons.org/publicdomain/mark/1.0/).
Each entry records the creator, the title, the original source and the licence. The
photos are not stored in the repository. The station downloads them at run time through
the [Openverse](https://openverse.org/) API's image proxy, processes them in memory and
never writes them to disk. No credit is legally required, but the creators are listed
in the catalogue with thanks.

## Methods and formulas

### UTCI polynomial

`intercom_webtab/weather/utci.py` computes the Universal Thermal Climate Index with the
operational 6th-order polynomial approximation:

> Bröde P., Fiala D., Błażejczyk K., Holmér I., Jendritzky G., Kampmann B., Tinz B.,
> Havenith G. (2012). Deriving the operational procedure for the Universal Thermal
> Climate Index (UTCI). *International Journal of Biometeorology* 56(3): 481–494.

The 210 regression constants are the published values from the public reference
program UTCI_a002 (P. Bröde, October 2009), distributed at
[utci.org](http://www.utci.org/) and released for public use. The surrounding code is a
clean-room implementation for this project, under MIT. Saturation vapour pressure uses
the ITS-90 formulation of Hardy (1998), as the reference program does.

### Thresholds

Weather scene and level thresholds follow public sources: WMO-No. 8, ICAO Annex 3, the
WHO UV index scale, the European Air Quality Index and the WHO 2021 air-quality
guidelines. They are facts used as parameters, and no text is copied from those sources.

## Software used, not bundled

These programs and libraries are installed separately (from your distribution or with
`pip`) and keep their own licences:

| Software | Licence |
|---|---|
| [Python](https://www.python.org/) | PSF License |
| [aiohttp](https://github.com/aio-libs/aiohttp) | Apache License 2.0 |
| [Pillow](https://python-pillow.org/) | MIT-CMU (HPND) |
| [FFmpeg](https://ffmpeg.org/), run as a separate process | LGPL 2.1+ / GPL 2+, depending on the build |
| [Asterisk](https://www.asterisk.org/), a separate server | GPL 2 |
| [nftables](https://netfilter.org/projects/nftables/) | GPL 2 |

The CI and the page tests download [acorn](https://github.com/acornjs/acorn) (MIT) and
[ESLint](https://eslint.org/) (MIT) with `npx` to check the page's JavaScript.
