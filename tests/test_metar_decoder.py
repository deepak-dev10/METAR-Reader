"""Unit tests for metar_decoder.

The decoder is a pure function, so these tests feed it hand-written mock
METAR readings and check that each group is interpreted correctly. Expected
values are worked out by hand from the METAR format, not copied from the
decoder's output.
"""

import pytest

from metar_decoder import (
    c_to_f,
    decode_metar,
    degrees_to_compass,
    ordinal,
    parse_fraction,
    parse_signed_temp,
    relative_humidity,
)


def metar(*groups):
    """Build a minimal mock METAR (station + time) followed by ``groups``."""
    return " ".join(["KTST", "281853Z", *groups])


# --- Helper functions -------------------------------------------------------

@pytest.mark.parametrize("degrees, expected", [
    (0, "north"),
    (11, "north"),             # just below the north / north-northeast boundary
    (12, "north-northeast"),   # just above it
    (45, "northeast"),
    (90, "east"),
    (135, "southeast"),
    (180, "south"),
    (225, "southwest"),
    (270, "west"),
    (315, "northwest"),
    (350, "north"),
    (360, "north"),            # 360° wraps round to north
])
def test_degrees_to_compass(degrees, expected):
    assert degrees_to_compass(degrees) == expected


@pytest.mark.parametrize("n, expected", [
    (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"),
    (11, "11th"), (12, "12th"), (13, "13th"),  # teen exceptions
    (21, "21st"), (22, "22nd"), (23, "23rd"), (31, "31st"),
])
def test_ordinal(n, expected):
    assert ordinal(n) == expected


@pytest.mark.parametrize("celsius, fahrenheit", [
    (0, 32), (100, 212), (-40, -40), (7, 45), (-5, 23), (37, 99),
])
def test_c_to_f(celsius, fahrenheit):
    assert c_to_f(celsius) == fahrenheit


def test_relative_humidity_is_100_when_dewpoint_equals_temperature():
    assert relative_humidity(15, 15) == 100


def test_relative_humidity_known_value():
    # 20°C with a 10°C dewpoint is about 53% humidity.
    assert relative_humidity(20, 10) == 53


@pytest.mark.parametrize("value, expected", [("07", 7), ("00", 0), ("M05", -5), ("M31", -31)])
def test_parse_signed_temp(value, expected):
    assert parse_signed_temp(value) == expected


@pytest.mark.parametrize("text, expected", [("10", 10.0), ("1/2", 0.5), ("1 1/2", 1.5), ("3/4", 0.75)])
def test_parse_fraction(text, expected):
    assert parse_fraction(text) == expected


# --- Header: report type, station, time, flags -----------------------------

@pytest.mark.parametrize("raw, report_type, station", [
    ("METAR KJFK 280551Z 02004KT", "METAR", "KJFK"),
    ("SPECI KSEA 280708Z 17012KT", "SPECI", "KSEA"),
    ("EGLL 281850Z 24008KT", "METAR", "EGLL"),  # no prefix defaults to METAR
])
def test_report_type_and_station(raw, report_type, station):
    decoded = decode_metar(raw)
    assert decoded["report_type"] == report_type
    assert decoded["station"] == station


@pytest.mark.parametrize("group, day, hour, minute, text", [
    ("280551Z", 28, 5, 51, "The 28th at 05:51 UTC"),
    ("010000Z", 1, 0, 0, "The 1st at 00:00 UTC"),
    ("222359Z", 22, 23, 59, "The 22nd at 23:59 UTC"),
])
def test_observation_time(group, day, hour, minute, text):
    time = decode_metar(f"KTST {group}")["time"]
    assert time == {"day": day, "hour": hour, "minute": minute, "text": text}


def test_auto_and_corrected_flags():
    decoded = decode_metar("KTST 281853Z COR AUTO 00000KT")
    assert decoded["auto"] is True
    assert decoded["corrected"] is True


def test_flags_default_to_false():
    decoded = decode_metar(metar("00000KT"))
    assert decoded["auto"] is False
    assert decoded["corrected"] is False


# --- Wind -------------------------------------------------------------------

@pytest.mark.parametrize("group, text", [
    ("00000KT", "Calm"),
    ("02004KT", "5 mph from the north-northeast (20°)"),         # 4 kt = 4.6 mph
    ("27015G28KT", "17 mph from the west (270°), gusting to 32 mph"),
    ("VRB03KT", "3 mph from variable directions"),
    ("18005MPS", "11 mph from the south (180°)"),                 # 5 m/s = 11.2 mph
    ("09020KMH", "12 mph from the east (90°)"),                   # 20 km/h = 12.4 mph
    ("360100KT", "115 mph from the north (360°)"),                # 3-digit speed
])
def test_wind_text(group, text):
    assert decode_metar(metar(group))["wind"]["text"] == text


def test_wind_fields_for_gusting_wind():
    wind = decode_metar(metar("27015G28KT"))["wind"]
    assert wind == {
        "direction_deg": 270,
        "direction_text": "west",
        "variable": False,
        "calm": False,
        "speed": 15,
        "unit": "KT",
        "speed_mph": 17,
        "gust_mph": 32,
        "text": "17 mph from the west (270°), gusting to 32 mph",
    }


def test_calm_wind_flags():
    wind = decode_metar(metar("00000KT"))["wind"]
    assert wind["calm"] is True
    assert wind["speed_mph"] == 0
    assert wind["gust_mph"] is None


def test_variable_wind_has_no_direction():
    wind = decode_metar(metar("VRB03KT"))["wind"]
    assert wind["variable"] is True
    assert wind["direction_deg"] is None
    assert wind["direction_text"] is None


def test_wind_direction_variation():
    decoded = decode_metar(metar("27015KT", "240V300"))
    assert decoded["wind_variation"] == "Varying between west-southwest (240°) and west-northwest (300°)"


def test_missing_wind_is_none():
    assert decode_metar(metar("10SM"))["wind"] is None


# --- Visibility -------------------------------------------------------------

@pytest.mark.parametrize("groups, miles, text", [
    (["10SM"], 10.0, "10 miles"),
    (["1SM"], 1.0, "1 mile"),
    (["1/2SM"], 0.5, "1/2 mile"),
    (["1", "1/2SM"], 1.5, "1 1/2 miles"),          # split over two groups
    (["M1/4SM"], 0.25, "Less than 1/4 mile"),
    (["P6SM"], 6.0, "More than 6 miles"),
    (["9999"], 6.2, "10 km or more (6+ miles)"),  # 9999 means 10 km or more
    (["0800"], 0.5, "800 meters (0.5 miles)"),
    (["4000"], 2.5, "4,000 meters (2.5 miles)"),
    (["CAVOK"], 6.2, "10 km or more (6+ miles)"),
])
def test_visibility(groups, miles, text):
    assert decode_metar(metar("00000KT", *groups))["visibility"] == {"miles": miles, "text": text}


def test_second_four_digit_group_is_not_treated_as_visibility():
    decoded = decode_metar(metar("10SM", "1234"))
    assert decoded["visibility"]["text"] == "10 miles"
    assert decoded["unparsed"] == ["1234"]


def test_lone_number_without_fraction_is_unparsed():
    decoded = decode_metar(metar("2", "10SM"))
    assert decoded["visibility"]["text"] == "10 miles"
    assert decoded["unparsed"] == ["2"]


# --- Present weather --------------------------------------------------------

@pytest.mark.parametrize("group, expected", [
    ("RA", "rain"),
    ("-RA", "light rain"),
    ("+RA", "heavy rain"),
    ("-SHRA", "light rain showers"),
    ("SHSN", "snow showers"),
    ("TS", "thunderstorm"),
    ("+TSRA", "heavy thunderstorm with rain"),
    ("VCTS", "thunderstorm nearby"),
    ("VCSH", "showers nearby"),
    ("FZFG", "freezing fog"),
    ("FZDZ", "freezing drizzle"),
    ("BLSN", "blowing snow"),
    ("MIFG", "shallow fog"),
    ("BCFG", "patches of fog"),
    ("BR", "mist"),
    ("HZ", "haze"),
    ("-RASN", "light rain and snow"),
    ("FC", "funnel cloud"),
    ("+FC", "tornado or waterspout"),
])
def test_weather(group, expected):
    assert decode_metar(metar(group))["weather"] == [expected]


def test_multiple_weather_groups_keep_their_order():
    assert decode_metar(metar("-RA", "BR"))["weather"] == ["light rain", "mist"]


# --- Clouds -----------------------------------------------------------------

@pytest.mark.parametrize("group, cover, height_ft, cloud_type, text", [
    ("FEW020", "FEW", 2000, None, "Few clouds at 2,000 ft"),
    ("SCT250", "SCT", 25000, None, "Scattered clouds at 25,000 ft"),
    ("BKN008", "BKN", 800, None, "Broken clouds at 800 ft"),
    ("OVC100", "OVC", 10000, None, "Overcast at 10,000 ft"),
    ("VV001", "VV", 100, None, "Sky obscured, vertical visibility at 100 ft"),
    ("BKN025CB", "BKN", 2500, "cumulonimbus (thunderstorm clouds)",
     "Broken clouds at 2,500 ft, cumulonimbus (thunderstorm clouds)"),
    ("SCT040TCU", "SCT", 4000, "towering cumulus", "Scattered clouds at 4,000 ft, towering cumulus"),
    ("OVC///", "OVC", None, None, "Overcast"),                         # height not measured
    ("BKN030///", "BKN", 3000, None, "Broken clouds at 3,000 ft"),    # type not measured
])
def test_cloud_layer(group, cover, height_ft, cloud_type, text):
    clouds = decode_metar(metar(group))["clouds"]
    assert clouds == [{"cover": cover, "height_ft": height_ft, "type": cloud_type, "text": text}]


def test_multiple_cloud_layers_keep_their_order():
    clouds = decode_metar(metar("SCT017", "BKN025", "OVC038"))["clouds"]
    assert [c["height_ft"] for c in clouds] == [1700, 2500, 3800]


@pytest.mark.parametrize("group", ["CLR", "SKC", "NSC", "NCD"])
def test_clear_sky_codes(group):
    decoded = decode_metar(metar(group))
    assert decoded["sky_clear"] is True
    assert decoded["clouds"] == []
    assert decoded["sky"] == "Clear skies"


# --- Temperature and dewpoint -----------------------------------------------

@pytest.mark.parametrize("group, temp_c, temp_f, dew_c, dew_f", [
    ("07/05", 7, 45, 5, 41),
    ("M02/M05", -2, 28, -5, 23),
    ("00/M01", 0, 32, -1, 30),
    ("M31/M34", -31, -24, -34, -29),
    ("32/02", 32, 90, 2, 36),
])
def test_temperature_and_dewpoint(group, temp_c, temp_f, dew_c, dew_f):
    temperature = decode_metar(metar(group))["temperature"]
    assert temperature["temp_c"] == temp_c
    assert temperature["temp_f"] == temp_f
    assert temperature["dew_c"] == dew_c
    assert temperature["dew_f"] == dew_f


def test_humidity_is_included():
    assert decode_metar(metar("20/10"))["temperature"]["humidity"] == 53


def test_temperature_without_dewpoint():
    temperature = decode_metar(metar("15/"))["temperature"]
    assert temperature == {"temp_c": 15, "temp_f": 59, "dew_c": None, "dew_f": None, "humidity": None}


# --- Pressure ---------------------------------------------------------------

@pytest.mark.parametrize("group, inhg, hpa, text", [
    ("A3043", 30.43, 1030, "30.43 inHg (1030 hPa)"),
    ("A2992", 29.92, 1013, "29.92 inHg (1013 hPa)"),   # standard pressure
    ("Q1013", 29.91, 1013, "1013 hPa (29.91 inHg)"),
    ("Q0998", 29.47, 998, "998 hPa (29.47 inHg)"),
])
def test_pressure(group, inhg, hpa, text):
    assert decode_metar(metar(group))["pressure"] == {"inhg": inhg, "hpa": hpa, "text": text}


# --- Robustness -------------------------------------------------------------

@pytest.mark.parametrize("marker", ["RMK", "TEMPO", "BECMG", "NOSIG"])
def test_decoding_stops_at_remarks_and_trends(marker):
    # The groups after the marker look like weather, but must be ignored.
    decoded = decode_metar(metar("10SM", "CLR", marker, "-RA", "OVC005"))
    assert decoded["weather"] == []
    assert decoded["clouds"] == []
    assert decoded["unparsed"] == []


@pytest.mark.parametrize("group", ["R04L/2000FT", "R22/P6000FT", "////", "///"])
def test_runway_range_and_missing_data_are_skipped(group):
    decoded = decode_metar(metar(group, "10SM"))
    assert decoded["unparsed"] == []
    assert decoded["visibility"]["miles"] == 10.0


def test_unknown_groups_are_collected_not_raised():
    decoded = decode_metar(metar("10SM", "XYZZY", "CLR"))
    assert decoded["unparsed"] == ["XYZZY"]
    assert decoded["sky"] == "Clear skies"


@pytest.mark.parametrize("raw", ["", "   ", "KJFK"])
def test_empty_or_station_only_report(raw):
    decoded = decode_metar(raw)
    assert decoded["summary"] == "No weather details available in this report."
    assert decoded["wind"] is None
    assert decoded["temperature"] is None


def test_raw_text_is_stripped():
    assert decode_metar("  KTST 281853Z 00000KT \n")["raw"] == "KTST 281853Z 00000KT"


# --- Summary rules ----------------------------------------------------------

@pytest.mark.parametrize("groups, sky", [
    (["FEW010"], "Mostly clear"),
    (["SCT010"], "Partly cloudy"),
    (["BKN010"], "Mostly cloudy"),
    (["OVC010"], "Overcast"),
    (["FEW010", "OVC020", "SCT030"], "Overcast"),   # most-covered layer wins
    (["OVC010", "VV005"], "Sky obscured"),          # obscured ranks above overcast
    (["CAVOK"], "Clear skies"),
    ([], None),                                     # no sky information at all
])
def test_sky_uses_most_covered_layer(groups, sky):
    assert decode_metar(metar(*groups))["sky"] == sky


def test_weather_without_sky_is_capitalized():
    assert decode_metar(metar("-RA"))["summary"] == "Light rain."


def test_temperature_only_summary():
    assert decode_metar(metar("10/05"))["summary"] == "50°F."


@pytest.mark.parametrize("groups, mentions_visibility", [
    (["10SM"], False),
    (["3SM"], False),             # exactly 3 miles is not mentioned
    (["2", "1/2SM"], True),
    (["M1/4SM"], True),
    (["0800"], True),
])
def test_visibility_only_in_summary_when_under_3_miles(groups, mentions_visibility):
    summary = decode_metar(metar("00000KT", *groups, "CLR", "10/05"))["summary"]
    assert ("visibility" in summary) is mentions_visibility


# --- Realistic mock readings ------------------------------------------------
# Complete METARs for a range of conditions, with the exact summary expected.

SCENARIOS = [
    pytest.param(
        "METAR KPHX 281853Z 00000KT 10SM CLR 32/02 A2990 RMK AO2",
        "Clear skies, 90°F, calm winds.",
        id="phoenix-clear-calm",
    ),
    pytest.param(
        "SPECI KSEA 280708Z 17012KT 10SM -RA SCT017 BKN025 OVC038 08/07 A2988 RMK AO2",
        "Overcast with light rain, 46°F, winds 14 mph from the south.",
        id="seattle-light-rain",
    ),
    pytest.param(
        "METAR KORD 281851Z 36025G35KT 1/4SM +SN BLSN VV005 M08/M10 A2975",
        "Sky obscured with heavy snow and blowing snow, 18°F, "
        "winds 29 mph from the north gusting to 40 mph, visibility 1/4 mile.",
        id="chicago-blizzard",
    ),
    pytest.param(
        "METAR KSFO 281456Z 28006KT 1/2SM FG OVC002 12/12 A3001",
        "Overcast with fog, 54°F, winds 7 mph from the west, visibility 1/2 mile.",
        id="san-francisco-fog",
    ),
    pytest.param(
        "METAR KMIA 281853Z 18020G35KT 3SM +TSRA BKN025CB OVC050 26/24 A2985",
        "Overcast with heavy thunderstorm with rain, 79°F, winds 23 mph from the south gusting to 40 mph.",
        id="miami-thunderstorm",
    ),
    pytest.param(
        "METAR EGLL 281850Z 24008KT CAVOK 13/09 Q1013 NOSIG",
        "Clear skies, 55°F, winds 9 mph from the west-southwest.",
        id="london-cavok",
    ),
    pytest.param(
        "METAR KDEN 281853Z 27015G28KT 240V300 10SM FEW080 15/M02 A3001",
        "Mostly clear, 59°F, winds 17 mph from the west gusting to 32 mph.",
        id="denver-gusty",
    ),
    pytest.param(
        "METAR PAFA 281853Z AUTO 00000KT 10SM SKC M31/M34 A3045",
        "Clear skies, -24°F, calm winds.",
        id="fairbanks-below-freezing",
    ),
    pytest.param(
        "METAR KBOS 281854Z VRB03KT 10SM SCT050 BKN100 18/10 A3012",
        "Mostly cloudy, 64°F, light and variable winds at 3 mph.",
        id="boston-variable-wind",
    ),
    pytest.param(
        "METAR OMDB 281800Z 33010KT 5000 HZ 35/15 Q1008",
        "Haze, 95°F, winds 12 mph from the north-northwest.",
        id="dubai-haze-no-clouds",
    ),
]


@pytest.mark.parametrize("raw, summary", SCENARIOS)
def test_realistic_reports(raw, summary):
    decoded = decode_metar(raw)
    assert decoded["summary"] == summary
    assert decoded["unparsed"] == []


def test_full_report_fields():
    """Check every decoded field for one complete report."""
    d = decode_metar("SPECI KSEA 280708Z 17012KT 10SM -RA SCT017 BKN025 OVC038 08/07 A2988 RMK AO2")
    assert d["report_type"] == "SPECI"
    assert d["station"] == "KSEA"
    assert d["time"]["text"] == "The 28th at 07:08 UTC"
    assert d["wind"]["text"] == "14 mph from the south (170°)"
    assert d["visibility"] == {"miles": 10.0, "text": "10 miles"}
    assert d["weather"] == ["light rain"]
    assert [c["text"] for c in d["clouds"]] == [
        "Scattered clouds at 1,700 ft",
        "Broken clouds at 2,500 ft",
        "Overcast at 3,800 ft",
    ]
    assert d["temperature"] == {"temp_c": 8, "temp_f": 46, "dew_c": 7, "dew_f": 45, "humidity": 93}
    assert d["pressure"]["text"] == "29.88 inHg (1012 hPa)"
    assert d["sky"] == "Overcast"
    assert d["cavok"] is False
    assert d["unparsed"] == []
