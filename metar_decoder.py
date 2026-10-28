"""Decode raw METAR weather reports into plain English.

A METAR looks like this:

    METAR KJFK 280551Z 02004KT 10SM SCT250 07/05 A3043 RMK AO2 SLP304

Each space-separated group describes one thing (time, wind, visibility,
clouds, temperature, pressure...). We walk through the groups, recognise
each one with a regular expression, and turn it into readable text.
"""

import math
import re

KNOTS_TO_MPH = 1.15078
MPS_TO_MPH = 2.23694
KMH_TO_MPH = 0.621371
METERS_PER_MILE = 1609.34
HPA_PER_INHG = 33.8639

COMPASS_POINTS = [
    "north", "north-northeast", "northeast", "east-northeast",
    "east", "east-southeast", "southeast", "south-southeast",
    "south", "south-southwest", "southwest", "west-southwest",
    "west", "west-northwest", "northwest", "north-northwest",
]

INTENSITY = {"-": "light", "+": "heavy"}

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

PHENOMENA = {
    "DZ": "drizzle", "RA": "rain", "SN": "snow", "SG": "snow grains",
    "IC": "ice crystals", "PL": "ice pellets", "GR": "hail", "GS": "small hail",
    "UP": "unknown precipitation", "BR": "mist", "FG": "fog", "FU": "smoke",
    "VA": "volcanic ash", "DU": "dust", "SA": "sand", "HZ": "haze",
    "PY": "spray", "PO": "dust whirls", "SQ": "squalls", "FC": "funnel cloud",
    "SS": "sandstorm", "DS": "duststorm",
}

CLOUD_COVER = {
    "FEW": "Few clouds",
    "SCT": "Scattered clouds",
    "BKN": "Broken clouds",
    "OVC": "Overcast",
    "VV": "Sky obscured, vertical visibility",
}

CLOUD_TYPES = {"CB": "cumulonimbus (thunderstorm clouds)", "TCU": "towering cumulus"}

# How the sky looks overall, keyed by the most-covered cloud layer.
SKY_SUMMARY = {
    "CLR": "Clear skies",
    "FEW": "Mostly clear",
    "SCT": "Partly cloudy",
    "BKN": "Mostly cloudy",
    "OVC": "Overcast",
    "VV": "Sky obscured",
}
COVER_RANK = ["CLR", "FEW", "SCT", "BKN", "OVC", "VV"]

CLEAR_CODES = {"SKC", "CLR", "NSC", "NCD"}
TREND_CODES = {"TEMPO", "BECMG", "NOSIG"}

TIME_RE = re.compile(r"^(?P<day>\d{2})(?P<hour>\d{2})(?P<minute>\d{2})Z$")
WIND_RE = re.compile(
    r"^(?P<dir>\d{3}|VRB)(?P<speed>\d{2,3})(?:G(?P<gust>\d{2,3}))?(?P<unit>KT|MPS|KMH)$"
)
WIND_VARIATION_RE = re.compile(r"^(?P<from>\d{3})V(?P<to>\d{3})$")
VIS_MILES_RE = re.compile(r"^(?P<prefix>[MP])?(?P<value>\d+(?:/\d+)?)SM$")
VIS_METERS_RE = re.compile(r"^(?P<meters>\d{4})(?:NDV)?$")
RUNWAY_RE = re.compile(r"^R\d{2}[LRC]?/")
WEATHER_RE = re.compile(
    r"^(?P<intensity>[-+]|VC)?"
    r"(?P<descriptor>MI|PR|BC|DR|BL|SH|TS|FZ)?"
    r"(?P<phenomena>(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)*)$"
)
CLOUD_RE = re.compile(r"^(?P<cover>FEW|SCT|BKN|OVC|VV)(?P<height>\d{3}|///)(?P<type>CB|TCU|///)?$")
TEMP_RE = re.compile(r"^(?P<temp>M?\d{2})/(?P<dew>M?\d{2})?$")
ALTIMETER_RE = re.compile(r"^A(?P<inhg>\d{4})$")
QNH_RE = re.compile(r"^Q(?P<hpa>\d{4})$")


def degrees_to_compass(degrees):
    """Turn a wind direction in degrees into a compass point like 'south-southwest'."""
    return COMPASS_POINTS[round((degrees % 360) / 22.5) % 16]


def ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def c_to_f(celsius):
    return round(celsius * 9 / 5 + 32)


def relative_humidity(temp_c, dew_c):
    """Approximate relative humidity (%) from temperature and dewpoint (Magnus formula)."""
    def vapor(t):
        return math.exp(17.625 * t / (243.04 + t))
    return round(100 * vapor(dew_c) / vapor(temp_c))


def parse_signed_temp(value):
    """METAR writes negative temperatures with an 'M' prefix, e.g. M05 = -5°C."""
    return -int(value[1:]) if value.startswith("M") else int(value)


def parse_fraction(text):
    """Parse '10', '1/2' or '1 1/2' into a float."""
    total = 0.0
    for part in text.split():
        if "/" in part:
            num, den = part.split("/")
            total += int(num) / int(den)
        else:
            total += int(part)
    return total


def parse_time(match):
    day, hour, minute = int(match["day"]), int(match["hour"]), int(match["minute"])
    return {
        "day": day,
        "hour": hour,
        "minute": minute,
        "text": f"The {ordinal(day)} at {hour:02d}:{minute:02d} UTC",
    }


def parse_wind(match):
    unit = match["unit"]
    to_mph = {"KT": KNOTS_TO_MPH, "MPS": MPS_TO_MPH, "KMH": KMH_TO_MPH}[unit]
    speed = int(match["speed"])
    gust = int(match["gust"]) if match["gust"] else None
    wind = {
        "direction_deg": None,
        "direction_text": None,
        "variable": match["dir"] == "VRB",
        "calm": speed == 0,
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
    meters = int(match["meters"])
    miles = round(meters / METERS_PER_MILE, 1)
    if meters == 9999:
        return {"miles": miles, "text": "10 km or more (6+ miles)"}
    return {"miles": miles, "text": f"{meters:,} meters ({miles:g} miles)"}


def parse_weather(match):
    """Turn a weather group like '-SHRA' or '+TSRA' into 'light rain showers' etc."""
    intensity = match["intensity"]
    descriptor = match["descriptor"]
    codes = re.findall(r"..", match["phenomena"])
    phenomena = " and ".join(PHENOMENA[c] for c in codes)

    if intensity == "+" and codes == ["FC"]:
        return "tornado or waterspout"

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
    cover = match["cover"]
    height = None if match["height"] == "///" else int(match["height"]) * 100
    cloud_type = CLOUD_TYPES.get(match["type"] or "")
    text = CLOUD_COVER[cover]
    if height is not None:
        text += f" at {height:,} ft"
    if cloud_type:
        text += f" ({cloud_type})"
    return {"cover": cover, "height_ft": height, "type": cloud_type, "text": text}


def parse_temperature(match):
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


def sky_condition(decoded):
    """Overall sky description based on the most-covered cloud layer."""
    if decoded["cavok"]:
        return "Clear skies"
    if decoded["clouds"]:
        worst = max(decoded["clouds"], key=lambda c: COVER_RANK.index(c["cover"]))
        return SKY_SUMMARY[worst["cover"]]
    if decoded["sky_clear"]:
        return SKY_SUMMARY["CLR"]
    return None


def summarize(decoded):
    """Build a friendly one-line summary, e.g. 'Partly cloudy, 45°F, winds 5 mph from the north.'"""
    parts = []

    sky = decoded["sky"]
    weather = " and ".join(decoded["weather"])
    if sky and weather:
        parts.append(f"{sky} with {weather}")
    elif sky or weather:
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


def decode_metar(raw):
    """Decode a raw METAR string into a dictionary of readable weather details."""
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
    if tokens and tokens[0] in ("METAR", "SPECI"):
        decoded["report_type"] = tokens.pop(0)
    if tokens:
        decoded["station"] = tokens.pop(0)

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
        elif token.isdigit() and len(token) <= 2 and i < len(tokens) and VIS_MILES_RE.match(tokens[i]):
            # Visibility split over two groups, e.g. "1 1/2SM"
            decoded["visibility"] = parse_visibility_miles(VIS_MILES_RE.match(tokens[i]), whole_miles=token)
            i += 1
        elif m := VIS_MILES_RE.match(token):
            decoded["visibility"] = parse_visibility_miles(m)
        elif decoded["visibility"] is None and (m := VIS_METERS_RE.match(token)):
            decoded["visibility"] = parse_visibility_meters(m)
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
