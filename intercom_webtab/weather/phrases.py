"""English texts of the weather panel: status-line phrase bank, condition words, scene
titles, widget names and small grammar helpers.

Limits (checked by tests/test_brain.py with worst-case numbers in every unit):
    MAXLEN  = 40  -- one status line (22 px type, 488 px wide)
    NOTELEN = 24  -- a widget note (12 px type, 152 px wide)

The brain decides *what* to say (fact key + parameters + condition flags); this module only
holds the wording. BANK maps a fact key to variants ``(template, condition)``: a variant is
used only when its condition flag is set (None = always allowed) and when every placeholder
has a value and the result fits MAXLEN. FALLBACK holds short texts used when no variant
fits. Templates are plain ``str.format`` strings.
"""
MAXLEN = 40
NOTELEN = 24

# ---------------------------------------------------------------- status-line phrase bank
BANK = {
    # P0 -- critical
    "rad_crit": [("Radiation {v} µSv/h — close windows", None),
                 ("Dose rate {v} µSv/h — shut windows", None)],
    "wind_crit": [("Storm: gusts to {g} {wu} — stay inside", "now"),
                  ("Gusts up to {g} {wu} — stay indoors", "now"),
                  ("Storm by {hh}, gusts up to {g} {wu}", "fc")],
    "air_crit": [("Hazardous air: AQI {aqi}, close windows", None),
                 ("Air is hazardous — close windows", None)],
    "dust_storm": [("Dust storm — close windows, stay in", None)],
    "frz_rain": [("Freezing rain — roads like an ice rink", "now"),
                 ("Freezing rain around {hh} — icy roads", "soon")],
    "hail": [("Hailstorm — wait it out indoors", "now"),
             ("Hailstorm soon — stay indoors", "soon0"),
             ("Hailstorm around {hh} — stay indoors", "soon")],
    "downpour": [("Torrential rain — wait it out inside", "now"),
                 ("Torrential rain around {hh}", "soon")],
    "heat4": [("Dangerous heat: feels like {utci}", None)],
    "cold4": [("Dangerous cold: feels like {utci}", None)],
    "uv4": [("Extreme UV {uv} — stay in the shade", None)],
    # P1 -- data failures
    "wx_stale": [("Weather not updated for {ago}", None),
                 ("Forecast is {ago} out of date", None)],
    "wx_none": [("Weather data has not loaded yet", None)],
    "air_stale": [("Air quality data is {ago} old", None),
                  ("Last air reading was {ago} ago", None)],
    "air_none": [("Air quality data did not load", None)],
    "rad_stale": [("Radiation not updated for {ago}", None),
                  ("Radiation data is {ago} old", None)],
    "rad_none": [("Radiation data did not load", None)],
    "cam_dead": [("{cam} camera silent since {hm}", "today"),
                 ("{cam} camera silent for {ago}", "long")],
    # P2 -- adverse
    "wind2": [("Gusts up to {g} {wu} — hold the door", None),
              ("Strong wind: gusts up to {g} {wu}", None)],
    "wind3": [("Gusts to {g} {wu} — keep away from trees", None),
              ("Very strong wind, gusts to {g} {wu}", None)],
    "wind_rise": [("Wind rising to {g} {wu} by {hh}", None)],
    "air2": [("Poor air, AQI {aqi} — keep windows shut", None),
             ("Polluted air — better keep windows shut", None)],
    "air3": [("Very poor air, AQI {aqi}", None),
             ("Very poor air — do not air the rooms", None)],
    "dust2": [("Dusty: PM10 {pm10} — keep windows shut", None),
              ("Dust haze — keep windows shut", None)],
    "o3": [("High ozone — skip daytime runs", None),
           ("Ozone above normal — no runs by day", None)],
    "no2": [("Traffic smog — air the rooms at night", None)],
    "rad2": [("Radiation above usual: {v} µSv/h", None)],
    "press_d24": [("Pressure fell {dp} {pu} in 24 hours", "fall"),
                  ("Pressure rose {dp} {pu} in 24 hours", "rise")],
    "press_fc": [("Pressure to drop {dp} {pu} {when}", "fall"),
                 ("Pressure to rise {dp} {pu} {when}", "rise")],
    "press_3h": [("Pressure falling fast: −{dp3} {pu} in 3 h", "fall"),
                 ("Pressure rising fast: +{dp3} {pu} in 3 h", "rise")],
    "press_abs": [("Very low pressure — {p} {pu}", "low"),
                  ("Very high pressure — {p} {pu}", "high")],
    "uv3": [("UV {uv} until {hh} — sunscreen and hat", None),
            ("Very high UV — sunscreen, glasses, hat", None)],
    "uv2": [("High UV — stay in shade around noon", "morning"),
            ("High UV until {hh} — wear a cap", None),
            ("High UV — cap and sunglasses", None)],
    "heat3": [("Very hot, feels like {utci} — drink water", None),
              ("Heat stress, feels like {utci}", None)],
    "heat2": [("Hot, feels like {utci} — drink water", None),
              ("Hot, feels like {utci} — seek shade", None)],
    "cold3": [("Feels like {utci} — wrap up warm", None),
              ("Severe cold: feels like {utci}", None)],
    "cold2": [("Cold: feels like {utci}", None),
              ("Feels like {utci} — dress warmly", None)],
    "ice": [("Black ice possible — take short steps", None),
            ("Slippery: rain, then frost", "wasrain")],
    "fog_dense": [("Dense fog, visibility {vis} {vu}", None)],
    "storm": [("Thunderstorm — shelter under a roof", "now"),
              ("Storm by {hh} — clear the balcony", "soon")],
    "heat_uv": [("Heat {utci} and UV {uv} — shade till {hh}", None)],
    "cold_wind": [("Cold wind — feels like {utci}", None)],
    "fog_ice": [("Fog and black ice — mind the steps", None)],
    # P3 -- precipitation
    "rain_end": [("Dry within the hour", None),
                 ("Snow stops within the hour", "snow")],
    "rain_end_t": [("Rain until ~{hh}, then dry", "rain"),
                   ("Rain easing off by {hh}", "rain"),
                   ("Snow until ~{hh}, then dry", "snow"),
                   ("Snow easing off by {hh}", "snow"),
                   ("Sleet until ~{hh}", "sleet")],
    "rain_long": [("Steady rain until ~{hh}", "rain"),
                  ("Rain set in — keep the umbrella handy", "rain"),
                  ("Steady snow until ~{hh}", "snow"),
                  ("Snowing — roads are slippery", "snow"),
                  ("Lasting sleet — slippery underfoot", "sleet")],
    "rain_now0": [("Rain is about to start", "rain_q"),
                  ("{Rainw} within the hour", "rain"),
                  ("{Rainw} starting before {hh}", "rain"),
                  ("Snow is about to start", "snow_q"),
                  ("Snow within the hour", "snow"),
                  ("Sleet within the hour", "sleet")],
    "rain_at": [("Rain starting around {hh}", "rain"),
                ("{Rainw} from around {hh}", "rain"),
                ("Snow starting around {hh}", "snow"),
                ("Snow from around {hh}", "snow"),
                ("Sleet from around {hh}", "sleet")],
    "shower": [("Shower around {hh} — best wait it out", "soon"),
               ("Heavy shower — best wait it out", "now")],
    "rain_maybe": [("{Rainw} possible soon", "rain"),
                   ("Rain possible around {hh}, {pp}%", "rain"),
                   ("{Snoww} possible soon", "snow"),
                   ("Snow possible around {hh}, {pp}%", "snow"),
                   ("Sleet possible soon", "sleet")],
    "rain_later": [("Rain {part}, {pp}%", "rain_hi"),
                   ("Chance of rain {part}, {pp}%", "rain_mid"),
                   ("Snow {part}, {pp}%", "snow_hi"),
                   ("Chance of snow {part}, {pp}%", "snow_mid"),
                   ("Sleet {part}, {pp}%", "sleet")],
    "rain_over": [("Rain has stopped, dry ahead", "rain"),
                  ("Snow has stopped, dry ahead", "snow")],
    # P4 -- moderate and combinations
    "hot_dry_uv": [("Hot and dry, high UV", None)],
    "muggy": [("Muggy and humid — air rooms at night", None)],
    "raw": [("Raw: damp and {t}", None)],
    "wind1": [("Breezy, gusts up to {g} {wu}", None),
              ("Gusty wind, up to {g} {wu}", None)],
    "press1": [("Pressure falling — weather may change", "fall"),
               ("Pressure rising — weather settling", "rise"),
               ("Pressure on the low side — {p} {pu}", "low"),
               ("Pressure on the high side — {p} {pu}", "high")],
    "air1": [("Moderate air — air rooms in the morning", None),
             ("Moderate air quality, AQI {aqi}", None)],
    "uv1": [("Strong sun — wear a cap at midday", "morning"),
            ("Sun still strong — wear a cap", "afternoon"),
            ("Moderate UV — wear a cap", None)],
    "rad1": [("{v} µSv/h at one station — watching", "single"),
             ("Radiation slightly up: {v} µSv/h", "multi")],
    "frost": [("Down to {tmin} tonight — frost likely", "evening"),
              ("Down to {tmin} by morning — frost risk", "night")],
    "swing": [("Up to {tmax} later — dress in layers", None),
              ("Warming up to {tmax} by midday", None)],
    "fog": [("Fog, visibility about {vis} {vu}", None),
            ("Fog — take care on the roads", None)],
    # P5 -- calm (a variant is not repeated within one day)
    "calm": [("Dry and calm, {t}", None),
             ("Quiet and dry, hardly any wind", "still"),
             ("No rain for the rest of the day", "dry_day"),
             ("Cloudy but dry until evening", "cloudy_dry"),
             ("Great weather for a walk", "walk"),
             ("Warm and still — good time to air out", "airing"),
             ("All calm: clean air, normal radiation", "clean"),
             ("Fresh but dry — a fine morning", "morning"),
             ("Clear evening, no rain", "clear_eve"),
             ("Quiet night, down to {tmin} by morning", "late"),
             ("Clear night, no rain", "clear_night"),
             ("Frosty and dry, {t}", "frosty"),
             ("Warm and dry, {t}", "warm"),
             ("Cool and dry, {t}", "cool"),
             ("No rain expected for 24 hours", "dry24"),
             ("Grey but calm and dry", "grey"),
             ("Sunny and dry, {t}", "sunny")],
    "wx_wait": [("Loading the weather…", None)],
}

# Short texts when no variant fits (tried in order; the first that formats and fits wins)
FALLBACK = {
    "rad_crit": ["Radiation above normal — close windows"],
    "wind_crit": ["Storm — stay indoors"],
    "air_crit": ["Air is hazardous — close windows"],
    "frz_rain": ["Freezing rain — icy roads"],
    "hail": ["Hailstorm — stay indoors"],
    "downpour": ["Torrential rain"],
    "heat4": ["Dangerous heat"],
    "cold4": ["Dangerous cold"],
    "uv4": ["Extreme UV — stay in the shade"],
    "wx_stale": ["Weather data is out of date"],
    "air_stale": ["Air quality data is out of date"],
    "rad_stale": ["Radiation data is out of date"],
    "cam_dead": ["{cam} camera not responding", "A camera is not responding"],
    "wind2": ["Strong wind"],
    "wind3": ["Very strong wind"],
    "wind_rise": ["Wind getting stronger"],
    "uv3": ["Very high UV — sunscreen and hat"],
    "uv2": ["High UV — cap and sunglasses"],
    "heat_uv": ["Heat and high UV — stay in the shade"],
    "press_d24": ["Big pressure change"],
    "press_fc": ["Pressure change ahead"],
    "press_3h": ["Pressure changing fast"],
    "press_abs": ["Pressure far from normal"],
    "press1": ["Pressure changing"],
    "rad1": ["Radiation slightly up"],
    "rad2": ["Radiation above usual"],
    "storm": ["Thunderstorm — shelter under a roof"],
    "rain_end": ["Rain will stop soon"],
    "rain_end_t": ["Precipitation ending soon"],
    "rain_long": ["Precipitation for a while"],
    "rain_now0": ["Precipitation soon"],
    "rain_at": ["Precipitation soon"],
    "shower": ["Shower — best wait it out"],
    "rain_maybe": ["Precipitation possible soon"],
    "rain_later": ["Precipitation possible later"],
    "rain_over": ["Precipitation has stopped"],
    "raw": ["Raw and damp"],
    "wind1": ["Breezy"],
    "frost": ["Frost possible tonight"],
    "swing": ["Much warmer later — dress in layers"],
    "fog": ["Fog — take care on the roads"],
    "fog_dense": ["Dense fog"],
    "air1": ["Moderate air quality"],
    "uv1": ["Moderate UV — wear a cap"],
    "calm": ["Dry and calm"],
    "cold_wind": ["Cold wind"],
    "heat3": ["Very hot — drink water"],
    "heat2": ["Hot — drink more water"],
    "cold3": ["Severe cold — wrap up warm"],
    "cold2": ["Cold — dress warmly"],
}

# ---------------------------------------------------------------- condition words (UTCI)
# Category ids of the UTCI assessment scale (Bröde et al. 2012), warmest first:
UTCI_BANDS = (            # (lower bound degC, inclusive?, category id)
    (46.0, False, "extreme_heat"),     # > 46  extreme heat stress
    (38.0, True, "very_hot"),          # 38..46 very strong heat stress
    (32.0, True, "hot"),               # 32..38 strong heat stress
    (26.0, True, "warm"),              # 26..32 moderate heat stress
    (9.0, True, "comfortable"),        # 9..26 no thermal stress
    (0.0, True, "cool"),               # 0..9 slight cold stress
    (-13.0, True, "cold"),             # -13..0 moderate cold stress
    (-27.0, True, "very_cold"),        # -27..-13 strong cold stress
    (-40.0, True, "bitter_cold"),      # -40..-27 very strong cold stress
)
UTCI_BOTTOM = "extreme_cold"           # < -40 extreme cold stress

WORDS = {
    "extreme_heat": "Extreme heat", "very_hot": "Very hot", "hot": "Hot", "warm": "Warm",
    "muggy": "Muggy", "hot_humid": "Hot and humid", "hot_dry": "Hot and dry",
    "comfortable": "Comfortable", "fresh": "Fresh", "damp": "Damp", "cool": "Cool", "raw": "Raw",
    "cold": "Cold", "frosty": "Frosty", "very_cold": "Very cold", "hard_frost": "Hard frost",
    "bitter_cold": "Bitter cold", "severe_frost": "Severe frost", "extreme_cold": "Extreme frost",
}

# ---------------------------------------------------------------- scenes (39 dot icons)
SCENES = [
    ("clear-day", "Clear"), ("clear-night", "Clear night"), ("mostly-clear-day", "Mostly clear"),
    ("mostly-clear-night", "Mostly clear night"), ("partly-cloudy-day", "Partly cloudy"),
    ("partly-cloudy-night", "Partly cloudy night"), ("overcast", "Overcast"),
    ("mostly-cloudy-day", "Mostly cloudy"), ("mostly-cloudy-night", "Mostly cloudy night"),
    ("fog", "Fog"), ("rime-fog", "Freezing fog"), ("drizzle", "Drizzle"), ("rain", "Rain"),
    ("heavy-rain", "Heavy rain"), ("freezing-drizzle", "Freezing drizzle"),
    ("freezing-rain", "Freezing rain"), ("showers-day", "Showers"),
    ("showers-night", "Night showers"), ("sleet", "Sleet"), ("snow", "Snow"),
    ("heavy-snow", "Heavy snow"), ("snow-grains", "Snow grains"),
    ("snow-showers-day", "Snow showers"), ("snow-showers-night", "Night snow showers"),
    ("thunderstorm", "Thunderstorm"), ("thunderstorm-heavy", "Severe thunderstorm"),
    ("thunderstorm-hail", "Thunderstorm with hail"), ("wind", "Strong wind"),
    ("blizzard", "Blizzard"), ("drifting-snow", "Drifting snow"), ("dust-haze", "Dust haze"),
    ("dust-storm", "Dust storm"), ("smoke", "Smoke"), ("dry-wind", "Hot dry wind"),
    ("haze", "Haze"), ("heat", "Heat"), ("frost", "Severe frost"), ("ice", "Black ice"),
    ("not-available", "No data"),
]
SCENE_TITLES = dict(SCENES)

# ---------------------------------------------------------------- widgets
WIDGET_NAMES = {"wind": "Wind", "air": "Air", "uv": "UV index", "pressure": "Pressure",
                "radiation": "Radiation", "sun": "Sun", "humidity": "Humidity"}
NO_DATA = "no data"
DIRECTIONS = ["north", "north-east", "east", "south-east", "south", "south-west", "west",
              "north-west"]
CALM = "calm"
POLLUTANTS = {"pm25": "PM2.5", "pm10": "PM10", "o3": "Ozone", "no2": "NO₂"}
AIR_WORDS = {0: "good", 1: "moderate", 2: "poor", 3: "very poor", 4: "extremely poor"}
UV_UNIT = "of 11"
RAD_UNIT = "µSv/h"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# when a forecast pressure change peaks, by part of day
WHEN = {"morning": "by morning", "afternoon": "by afternoon", "evening": "by evening",
        "night": "overnight", None: "within a day"}


# ---------------------------------------------------------------- grammar helpers
def plural(n, one, many):
    return one if abs(int(n)) == 1 else many


def count(n, one, many):
    return "%d %s" % (n, plural(n, one, many))


def ago(sec):
    """How long: '45 minutes', '29 hours', '3 days'."""
    sec = max(0, int(sec))
    m = int(round(sec / 60.0))
    if m < 90:
        return count(max(1, m), "minute", "minutes")
    if sec < 48 * 3600:
        h = max(2, int(round(sec / 3600.0)))
        if h >= 48:
            return "2 days"
        return count(h, "hour", "hours")
    return count(int(sec // 86400), "day", "days")


def part_of_day(hour):
    """Part-of-day id for a local hour."""
    if 5 <= hour <= 10:
        return "morning"
    if 11 <= hour <= 16:
        return "afternoon"
    if 17 <= hour <= 22:
        return "evening"
    return "night"


_PART_TODAY = {"morning": "this morning", "afternoon": "this afternoon",
               "evening": "this evening", "night": "tonight"}
_PART_NEXT = {"morning": "in the morning", "afternoon": "in the afternoon",
              "evening": "in the evening", "night": "tonight"}


def part_phrase(part, rel):
    """'this morning' / 'tomorrow morning' / 'in the morning' (event tomorrow, said late at
    night); the night after midnight is always 'tonight'."""
    if part == "night":
        return "tonight"
    if rel == "tomorrow":
        return "tomorrow " + part
    if rel == "next":
        return _PART_NEXT[part]
    return _PART_TODAY[part]


def precip_adj(mm_per_h, heavy=True):
    """Adjective for a rain rate (mm/h): 'light ' below 0.6, 'heavy ' from 1.9."""
    if mm_per_h < 0.6:
        return "light "
    if heavy and mm_per_h >= 1.9:
        return "heavy "
    return ""


def cap(s):
    return s[:1].upper() + s[1:] if s else s


def of_stations(k, n):
    """'2 of 8 stations', '1 of 1 station'."""
    return "%d of %d %s" % (k, n, plural(n, "station", "stations"))


def stations_normal(n):
    return "normal, " + count(n, "station", "stations")


RAD_REPEAT = "second day running"
RAD_EDGE = "near the limit"


def as_of_time(hh, mm):
    return "as of %02d:%02d" % (hh, mm)


def as_of_date(d):
    return "as of %d %s" % (d.day, MONTHS[d.month - 1])


def gusts(g):
    return "gusts %s" % g


def gusts_by(g, hh):
    return "gusts %s by %s" % (g, hh)


def uv_peak(n, tomorrow):
    return "up to %d %s" % (n, "tomorrow" if tomorrow else "today")


def press_note(kind, value=None, hh=None):
    """Pressure widget note: what drives the level (all values already formatted)."""
    if kind == "d24":
        return "%s in 24 h" % value
    if kind == "fc_at":
        return "%s by %s" % (value, hh)
    if kind == "fc_day":
        return "%s by tomorrow" % value
    if kind == "3h":
        return "%s in 3 h" % value
    if kind == "below":
        return "%s below normal" % value
    if kind == "above":
        return "%s above normal" % value
    if kind == "same":
        return "no change"
    return ""


def sun_note(kind, hm):
    return "%s %s" % ("sunset" if kind == "set" else "sunrise", hm)


def sun_unit(kind):
    return "sunrise" if kind == "rise" else "sunset"


def dew_note(t):
    return "dew point %s" % t


def air_note(culprit=None, level=0):
    if culprit:
        return "%s elevated" % POLLUTANTS[culprit]
    return AIR_WORDS[min(4, max(0, level))]
