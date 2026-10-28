"""Decode raw METAR weather reports into plain English.

METAR is the standard format airports use to report current weather. A
report is a single line of space-separated groups, for example::

    METAR KJFK 280551Z 02004KT 10SM SCT250 07/05 A3043 RMK AO2 SLP304

Each group describes one thing (observation time, wind, visibility, clouds,
temperature, pressure, ...). This module walks through the groups in order,
recognises each one with a regular expression, and converts it into readable
text and US-friendly units (°F, mph, miles).

Typical usage::

    from metar_decoder import decode_metar

    report = decode_metar("KLAX 281853Z 00000KT 10SM CLR 21/12 A2992")
    print(report["summary"])  # "Clear skies, 70°F, calm winds."

Only the main body of the report is decoded. The free-form remarks section
(everything after ``RMK``) and forecast trend groups (``TEMPO``, ``BECMG``,
``NOSIG``) are ignored.
"""

import math
import re

# --- Unit conversion factors ------------------------------------------------

KNOTS_TO_MPH = 1.15078
MPS_TO_MPH = 2.23694  # metres per second, used by some non-US stations
KMH_TO_MPH = 0.621371
METERS_PER_MILE = 1609.34
HPA_PER_INHG = 33.8639

# --- Lookup tables ----------------------------------------------------------

# 16-point compass, clockwise from north. Each point covers 22.5°.
COMPASS_POINTS = [
    "north", "north-northeast", "northeast", "east-northeast",
    "east", "east-southeast", "southeast", "south-southeast",
    "south", "south-southwest", "southwest", "west-southwest",
    "west", "west-northwest", "northwest", "north-northwest",
]

# Intensity prefixes for weather groups. No prefix means "moderate", and
# "VC" (vicinity) is handled separately because it reads better as a suffix.
INTENSITY = {"-": "light", "+": "heavy"}

# Descriptors modify the phenomenon that follows, e.g. FZ + RA = freezing rain.
DESCRIPTORS = {
    "MI": "shallow",
    "PR": "partial",
    "BC": "patches of",
    "DR": "low drifting",
    "BL": "blowing",
    "SH": "showers",
    "TS": "thunderstorm",
    "FZ": "freezing",
}

# Precipitation, obscuration and other weather phenomena.
PHENOMENA = {
    "DZ": "drizzle", "RA": "rain", "SN": "snow", "SG": "snow grains",
    "IC": "ice crystals", "PL": "ice pellets", "GR": "hail", "GS": "small hail",
    "UP": "unknown precipitation", "BR": "mist", "FG": "fog", "FU": "smoke",
    "VA": "volcanic ash", "DU": "dust", "SA": "sand", "HZ": "haze",
    "PY": "spray", "PO": "dust whirls", "SQ": "squalls", "FC": "funnel cloud",
    "SS": "sandstorm", "DS": "duststorm",
}

# Cloud cover amounts, measured in eighths (oktas) of the sky:
# FEW = 1-2, SCT = 3-4, BKN = 5-7, OVC = 8. VV means the sky is hidden
# (e.g. by fog) and the height given is how far up an observer can see.
CLOUD_COVER = {
    "FEW": "Few clouds",
    "SCT": "Scattered clouds",
    "BKN": "Broken clouds",
    "OVC": "Overcast",
    "VV": "Sky obscured, vertical visibility",
}

# Significant convective cloud types that may be appended to a cloud layer.
CLOUD_TYPES = {"CB": "cumulonimbus (thunderstorm clouds)", "TCU": "towering cumulus"}

# Everyday description of the sky, keyed by the most-covered cloud layer.
SKY_SUMMARY = {
    "CLR": "Clear skies",
    "FEW": "Mostly clear",
    "SCT": "Partly cloudy",
    "BKN": "Mostly cloudy",
    "OVC": "Overcast",
    "VV": "Sky obscured",
}

# Cover codes from least to most covered, used to pick the "worst" layer.
COVER_RANK = ["CLR", "FEW", "SCT", "BKN", "OVC", "VV"]

# Codes meaning "no clouds": sky clear, clear below 12,000 ft (automated),
# no significant cloud, and no cloud detected (automated).
CLEAR_CODES = {"SKC", "CLR", "NSC", "NCD"}

# Forecast trend groups. These mark the end of the current observation.
TREND_CODES = {"TEMPO", "BECMG", "NOSIG"}

# --- Group patterns ---------------------------------------------------------

# Day of month, hour and minute in UTC, e.g. 280551Z.
TIME_RE = re.compile(r"^(?P<day>\d{2})(?P<hour>\d{2})(?P<minute>\d{2})Z$")

# Direction (degrees or VRB), speed, optional gust and unit, e.g. 27015G28KT.
WIND_RE = re.compile(
    r"^(?P<dir>\d{3}|VRB)(?P<speed>\d{2,3})(?:G(?P<gust>\d{2,3}))?(?P<unit>KT|MPS|KMH)$"
)

# Range the wind direction is varying across, e.g. 240V300.
WIND_VARIATION_RE = re.compile(r"^(?P<from>\d{3})V(?P<to>\d{3})$")

# US visibility in statute miles, optionally with M (less than) or
# P (more than), e.g. 10SM, 1/2SM, M1/4SM, P6SM.
VIS_MILES_RE = re.compile(r"^(?P<prefix>[MP])?(?P<value>\d+(?:/\d+)?)SM$")

# International visibility in metres, e.g. 0800. 9999 means 10 km or more.
VIS_METERS_RE = re.compile(r"^(?P<meters>\d{4})(?:NDV)?$")

# Runway visual range, e.g. R04L/2000FT. Recognised only so it can be skipped.
RUNWAY_RE = re.compile(r"^R\d{2}[LRC]?/")

# Present weather: optional intensity, optional descriptor, then zero or
# more two-letter phenomena, e.g. -RA, +TSRA, VCSH, FZFG, -RASN.
WEATHER_RE = re.compile(
    r"^(?P<intensity>[-+]|VC)?"
    r"(?P<descriptor>MI|PR|BC|DR|BL|SH|TS|FZ)?"
    r"(?P<phenomena>(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)*)$"
)

# Cloud layer: cover amount plus height in hundreds of feet, with an optional
# cloud type, e.g. BKN025CB. "///" marks a value the station could not measure.
CLOUD_RE = re.compile(r"^(?P<cover>FEW|SCT|BKN|OVC|VV)(?P<height>\d{3}|///)(?P<type>CB|TCU|///)?$")

# Temperature/dewpoint in whole °C, with M for minus, e.g. 07/05 or M02/M05.
TEMP_RE = re.compile(r"^(?P<temp>M?\d{2})/(?P<dew>M?\d{2})?$")

# Altimeter setting in hundredths of inHg (US), e.g. A3043 = 30.43 inHg.
ALTIMETER_RE = re.compile(r"^A(?P<inhg>\d{4})$")

# Sea-level pressure in hectopascals (international), e.g. Q1013.
QNH_RE = re.compile(r"^Q(?P<hpa>\d{4})$")


# --- Helpers ----------------------------------------------------------------

def degrees_to_compass(degrees):
    """Convert a bearing in degrees to a 16-point compass name.

    Args:
        degrees: Bearing in degrees, where 0 is north and 90 is east.

    Returns:
        A compass point such as ``"south-southwest"``.
    """
    # Divide the circle into 16 slices of 22.5° and round to the nearest one.
    # The modulo wraps 360° (and anything just under it) back to north.
    return COMPASS_POINTS[round((degrees % 360) / 22.5) % 16]


def ordinal(n):
    """Return ``n`` with its English ordinal suffix, e.g. 1 -> "1st", 12 -> "12th"."""
    # 11th, 12th and 13th are exceptions to the usual 1st/2nd/3rd rule.
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def c_to_f(celsius):
    """Convert degrees Celsius to whole degrees Fahrenheit."""
    return round(celsius * 9 / 5 + 32)


def relative_humidity(temp_c, dew_c):
    """Estimate relative humidity from temperature and dewpoint.

    Uses the Magnus approximation for saturation vapour pressure, which is
    accurate to within a fraction of a percent for normal weather conditions.

    Args:
        temp_c: Air temperature in °C.
        dew_c: Dewpoint in °C.

    Returns:
        Relative humidity as a whole-number percentage.
    """
    def vapor(t):
        return math.exp(17.625 * t / (243.04 + t))
    return round(100 * vapor(dew_c) / vapor(temp_c))


def parse_signed_temp(value):
    """Parse a METAR temperature, where an ``M`` prefix means minus (M05 = -5)."""
    return -int(value[1:]) if value.startswith("M") else int(value)


def parse_fraction(text):
    """Parse a whole number, fraction or mixed number into a float.

    Args:
        text: A string such as ``"10"``, ``"1/2"`` or ``"1 1/2"``.

    Returns:
        The numeric value, e.g. ``1.5`` for ``"1 1/2"``.
    """
    total = 0.0
    for part in text.split():
        if "/" in part:
            num, den = part.split("/")
            total += int(num) / int(den)
        else:
            total += int(part)
    return total


# --- Group parsers ----------------------------------------------------------
# Each parser takes a regex match for one METAR group and returns a dict (or
# string) holding the decoded values together with a human-readable "text".

def parse_time(match):
    """Decode an observation time group such as ``280551Z``.

    METARs only include the day of the month, not the month or year.
    """
    day, hour, minute = int(match["day"]), int(match["hour"]), int(match["minute"])
    return {
        "day": day,
        "hour": hour,
        "minute": minute,
        "text": f"The {ordinal(day)} at {hour:02d}:{minute:02d} UTC",
    }


def parse_wind(match):
    """Decode a wind group such as ``02004KT``, ``27015G28KT`` or ``VRB03KT``.

    The direction is where the wind is blowing *from*, in degrees true.

    Returns:
        A dict with the direction, calm/variable flags, the original speed
        and unit, speeds converted to mph, and a readable ``text``.
    """
    unit = match["unit"]
    to_mph = {"KT": KNOTS_TO_MPH, "MPS": MPS_TO_MPH, "KMH": KMH_TO_MPH}[unit]
    speed = int(match["speed"])
    gust = int(match["gust"]) if match["gust"] else None
    wind = {
        "direction_deg": None,
        "direction_text": None,
        "variable": match["dir"] == "VRB",
        "calm": speed == 0,  # 00000KT
        "speed": speed,
        "unit": unit,
        "speed_mph": round(speed * to_mph),
        "gust_mph": round(gust * to_mph) if gust else None,
    }
    if not wind["variable"]:
        wind["direction_deg"] = int(match["dir"])
        wind["direction_text"] = degrees_to_compass(wind["direction_deg"])

    if wind["calm"]:
        text = "Calm"
    elif wind["variable"]:
        text = f"{wind['speed_mph']} mph from variable directions"
    else:
        text = f"{wind['speed_mph']} mph from the {wind['direction_text']} ({wind['direction_deg']}°)"
    if wind["gust_mph"]:
        text += f", gusting to {wind['gust_mph']} mph"
    wind["text"] = text
    return wind


def parse_visibility_miles(match, whole_miles=None):
    """Decode a statute-mile visibility group such as ``10SM`` or ``M1/4SM``.

    Args:
        match: Match from ``VIS_MILES_RE``.
        whole_miles: The whole-number part when visibility is written as two
            groups, e.g. ``"1"`` for ``1 1/2SM``.

    Returns:
        A dict with ``miles`` (float) and a readable ``text``.
    """
    value = match["value"]
    if whole_miles:
        value = f"{whole_miles} {value}"
    miles = parse_fraction(value)
    unit = "mile" if miles <= 1 else "miles"
    text = f"{value} {unit}"
    if match["prefix"] == "M":
        text = f"Less than {text}"
    elif match["prefix"] == "P":
        text = f"More than {text}"
    return {"miles": miles, "text": text}


def parse_visibility_meters(match):
    """Decode a metric visibility group such as ``0800`` or ``9999``.

    Returns:
        A dict with ``miles`` (float, rounded to 0.1) and a readable ``text``.
    """
    meters = int(match["meters"])
    miles = round(meters / METERS_PER_MILE, 1)
    # 9999 is a special value meaning "10 km or more", not exactly 9,999 m.
    if meters == 9999:
        return {"miles": miles, "text": "10 km or more (6+ miles)"}
    return {"miles": miles, "text": f"{meters:,} meters ({miles:g} miles)"}


def parse_weather(match):
    """Decode a present-weather group into lower-case English.

    Examples:
        ``-RA`` -> "light rain", ``+TSRA`` -> "heavy thunderstorm with rain",
        ``VCSH`` -> "showers nearby", ``FZFG`` -> "freezing fog".

    Returns:
        A short description of the weather.
    """
    intensity = match["intensity"]
    descriptor = match["descriptor"]
    # Phenomena are always two letters each, so "RASN" -> ["RA", "SN"].
    codes = re.findall(r"..", match["phenomena"])
    phenomena = " and ".join(PHENOMENA[c] for c in codes)

    # A heavy funnel cloud is the code for a tornado or waterspout.
    if intensity == "+" and codes == ["FC"]:
        return "tornado or waterspout"

    # TS and SH read naturally in a different position from other descriptors.
    if descriptor == "TS":
        text = f"thunderstorm with {phenomena}" if phenomena else "thunderstorm"
    elif descriptor == "SH":
        text = f"{phenomena} showers" if phenomena else "showers"
    elif descriptor:
        text = f"{DESCRIPTORS[descriptor]} {phenomena}".strip()
    else:
        text = phenomena

    if intensity in INTENSITY:
        text = f"{INTENSITY[intensity]} {text}"
    elif intensity == "VC":
        text = f"{text} nearby"
    return text


def parse_cloud(match):
    """Decode a cloud layer group such as ``SCT250`` or ``BKN025CB``.

    Returns:
        A dict with the ``cover`` code, ``height_ft`` (or None if not
        measured), the cloud ``type`` (or None), and a readable ``text``.
    """
    cover = match["cover"]
    # Heights are given in hundreds of feet above the ground.
    height = None if match["height"] == "///" else int(match["height"]) * 100
    cloud_type = CLOUD_TYPES.get(match["type"] or "")
    text = CLOUD_COVER[cover]
    if height is not None:
        text += f" at {height:,} ft"
    if cloud_type:
        text += f", {cloud_type}"
    return {"cover": cover, "height_ft": height, "type": cloud_type, "text": text}


def parse_temperature(match):
    """Decode a temperature/dewpoint group such as ``07/05`` or ``M02/M05``.

    The dewpoint is optional in the format, so dewpoint and humidity fields
    are None when it is missing.

    Returns:
        A dict with temperature and dewpoint in °C and °F, and humidity in %.
    """
    temp_c = parse_signed_temp(match["temp"])
    result = {
        "temp_c": temp_c,
        "temp_f": c_to_f(temp_c),
        "dew_c": None,
        "dew_f": None,
        "humidity": None,
    }
    if match["dew"]:
        dew_c = parse_signed_temp(match["dew"])
        result.update(dew_c=dew_c, dew_f=c_to_f(dew_c), humidity=relative_humidity(temp_c, dew_c))
    return result


# --- Summary ----------------------------------------------------------------

def sky_condition(decoded):
    """Describe the overall sky, e.g. "Partly cloudy", from decoded data.

    The description is based on the most-covered cloud layer, since that is
    what a person on the ground would notice.

    Args:
        decoded: A partially built result dict from ``decode_metar``.

    Returns:
        A short description, or None if the report has no sky information.
    """
    if decoded["cavok"]:
        return "Clear skies"
    if decoded["clouds"]:
        worst = max(decoded["clouds"], key=lambda c: COVER_RANK.index(c["cover"]))
        return SKY_SUMMARY[worst["cover"]]
    if decoded["sky_clear"]:
        return SKY_SUMMARY["CLR"]
    return None


def summarize(decoded):
    """Build a friendly one-line weather summary.

    The summary covers sky and weather, temperature and wind. Visibility is
    only mentioned when it is under 3 miles, which is when it matters.

    Example:
        "Overcast with light rain, 48°F, winds 12 mph from the south."

    Args:
        decoded: A result dict from ``decode_metar`` (with ``sky`` filled in).

    Returns:
        A sentence describing the weather.
    """
    parts = []

    sky = decoded["sky"]
    weather = " and ".join(decoded["weather"])
    if sky and weather:
        parts.append(f"{sky} with {weather}")
    elif sky or weather:
        # Weather descriptions are lower case, so capitalise when they lead.
        parts.append(sky or weather[0].upper() + weather[1:])

    temperature = decoded["temperature"]
    if temperature:
        parts.append(f"{temperature['temp_f']}°F")

    wind = decoded["wind"]
    if wind:
        if wind["calm"]:
            wind_text = "calm winds"
        elif wind["variable"]:
            wind_text = f"light and variable winds at {wind['speed_mph']} mph"
        else:
            wind_text = f"winds {wind['speed_mph']} mph from the {wind['direction_text']}"
        if wind["gust_mph"]:
            wind_text += f" gusting to {wind['gust_mph']} mph"
        parts.append(wind_text)

    visibility = decoded["visibility"]
    if visibility and visibility["miles"] < 3:
        parts.append(f"visibility {visibility['text'].lower()}")

    if not parts:
        return "No weather details available in this report."
    return ", ".join(parts) + "."


# --- Public API -------------------------------------------------------------

def decode_metar(raw):
    """Decode a raw METAR string into readable weather details.

    Groups the decoder does not recognise are collected in ``unparsed``
    instead of raising an error, so a single odd group never breaks the
    whole report.

    Args:
        raw: The METAR text, with or without the leading ``METAR``/``SPECI``
            report type, e.g. ``"METAR KJFK 280551Z 02004KT 10SM CLR 07/05 A3043"``.

    Returns:
        A dict with the keys:

        - ``raw``, ``report_type``, ``station``: the input and its header.
        - ``time``: observation time (see ``parse_time``), or None.
        - ``auto``: True if the report came from an automated station.
        - ``corrected``: True if this is a correction of an earlier report.
        - ``wind``: see ``parse_wind``, or None.
        - ``wind_variation``: text describing a variable direction, or None.
        - ``visibility``: dict with ``miles`` and ``text``, or None.
        - ``cavok``: True for "ceiling and visibility OK".
        - ``weather``: list of weather descriptions, e.g. ["light rain"].
        - ``clouds``: list of cloud layers (see ``parse_cloud``).
        - ``sky_clear``: True if the report explicitly says no clouds.
        - ``temperature``: see ``parse_temperature``, or None.
        - ``pressure``: dict with ``inhg``, ``hpa`` and ``text``, or None.
        - ``unparsed``: groups that were not recognised.
        - ``sky``: overall sky description (see ``sky_condition``).
        - ``summary``: friendly one-line summary (see ``summarize``).
    """
    decoded = {
        "raw": raw.strip(),
        "report_type": "METAR",
        "station": None,
        "time": None,
        "auto": False,
        "corrected": False,
        "wind": None,
        "wind_variation": None,
        "visibility": None,
        "cavok": False,
        "weather": [],
        "clouds": [],
        "sky_clear": False,
        "temperature": None,
        "pressure": None,
        "unparsed": [],
    }

    tokens = raw.split()
    # The report type is optional; the station identifier always comes next.
    if tokens and tokens[0] in ("METAR", "SPECI"):
        decoded["report_type"] = tokens.pop(0)
    if tokens:
        decoded["station"] = tokens.pop(0)

    # A manual index (rather than a for-loop) lets the visibility branch
    # consume two tokens at once for values like "1 1/2SM".
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1

        # Remarks and forecast trends come at the end; we stop there.
        if token == "RMK" or token in TREND_CODES:
            break
        if token == "AUTO":
            decoded["auto"] = True
        elif token == "COR":
            decoded["corrected"] = True
        elif token == "CAVOK":
            # "Ceiling and visibility OK": visibility 10 km+, no significant cloud.
            decoded["cavok"] = True
            decoded["visibility"] = {"miles": 6.2, "text": "10 km or more (6+ miles)"}
        elif token in CLEAR_CODES:
            decoded["sky_clear"] = True
        elif set(token) <= {"/"} or RUNWAY_RE.match(token):
            continue  # missing data or runway visual range — skip quietly
        elif m := TIME_RE.match(token):
            decoded["time"] = parse_time(m)
        elif m := WIND_RE.match(token):
            decoded["wind"] = parse_wind(m)
        elif m := WIND_VARIATION_RE.match(token):
            frm, to = int(m["from"]), int(m["to"])
            decoded["wind_variation"] = (
                f"Varying between {degrees_to_compass(frm)} ({frm}°) and {degrees_to_compass(to)} ({to}°)"
            )
        elif (
            token.isdigit() and len(token) <= 2 and i < len(tokens)
            and "/" in tokens[i] and VIS_MILES_RE.match(tokens[i])
        ):
            # Visibility split over two groups, e.g. "1 1/2SM". The second
            # group must be a fraction; "2 10SM" is not "2 10 miles".
            decoded["visibility"] = parse_visibility_miles(VIS_MILES_RE.match(tokens[i]), whole_miles=token)
            i += 1
        elif m := VIS_MILES_RE.match(token):
            decoded["visibility"] = parse_visibility_miles(m)
        # Only treat a bare 4-digit group as metric visibility if we haven't
        # already found visibility, so it can't be confused with other groups.
        elif decoded["visibility"] is None and (m := VIS_METERS_RE.match(token)):
            decoded["visibility"] = parse_visibility_meters(m)
        # Every part of WEATHER_RE is optional, so require at least a
        # descriptor or phenomenon to avoid matching things like a lone "-".
        elif (m := WEATHER_RE.match(token)) and (m["descriptor"] or m["phenomena"]):
            decoded["weather"].append(parse_weather(m))
        elif m := CLOUD_RE.match(token):
            decoded["clouds"].append(parse_cloud(m))
        elif m := TEMP_RE.match(token):
            decoded["temperature"] = parse_temperature(m)
        elif m := ALTIMETER_RE.match(token):
            inhg = int(m["inhg"]) / 100
            hpa = round(inhg * HPA_PER_INHG)
            decoded["pressure"] = {"inhg": inhg, "hpa": hpa, "text": f"{inhg:.2f} inHg ({hpa} hPa)"}
        elif m := QNH_RE.match(token):
            hpa = int(m["hpa"])
            inhg = round(hpa / HPA_PER_INHG, 2)
            decoded["pressure"] = {"inhg": inhg, "hpa": hpa, "text": f"{hpa} hPa ({inhg:.2f} inHg)"}
        else:
            decoded["unparsed"].append(token)

    decoded["sky"] = sky_condition(decoded)
    decoded["summary"] = summarize(decoded)
    return decoded
