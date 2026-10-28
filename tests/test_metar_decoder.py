import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as metar_app  # noqa: E402
from metar_decoder import decode_metar, degrees_to_compass  # noqa: E402


def test_basic_us_metar():
    d = decode_metar("METAR KJFK 280551Z 02004KT 10SM SCT250 07/05 A3043 RMK AO2 SLP304 T00720050")
    assert d["station"] == "KJFK"
    assert d["time"]["text"] == "The 28th at 05:51 UTC"
    assert d["wind"]["direction_text"] == "north-northeast"
    assert d["wind"]["speed_mph"] == 5
    assert d["visibility"]["text"] == "10 miles"
    assert d["clouds"][0]["text"] == "Scattered clouds at 25,000 ft"
    assert d["temperature"]["temp_f"] == 45
    assert d["pressure"]["inhg"] == 30.43
    assert d["unparsed"] == []
    assert d["summary"] == "Partly cloudy, 45°F, winds 5 mph from the north-northeast."


def test_clear_calm():
    d = decode_metar("KLAX 281853Z 00000KT 10SM CLR 21/12 A2992")
    assert d["wind"]["calm"]
    assert d["summary"] == "Clear skies, 70°F, calm winds."


def test_gusts_and_variable_direction():
    d = decode_metar("KDEN 281853Z 27015G28KT 240V300 10SM FEW080 15/M02 A3001")
    assert d["wind"]["gust_mph"] == 32
    assert d["wind_variation"].startswith("Varying between west-southwest")
    assert "gusting to 32 mph" in d["summary"]


def test_variable_light_wind():
    d = decode_metar("KBOS 281853Z VRB03KT 10SM BKN050 12/08 A3010")
    assert d["wind"]["variable"]
    assert "light and variable winds at 3 mph" in d["summary"]
    assert d["sky"] == "Mostly cloudy"


def test_fractional_visibility_and_weather():
    d = decode_metar("KORD 281853Z 09012KT 1 1/2SM -RA BR OVC008 04/03 A2988")
    assert d["visibility"]["miles"] == 1.5
    assert d["visibility"]["text"] == "1 1/2 miles"
    assert d["weather"] == ["light rain", "mist"]
    assert d["summary"].startswith("Overcast with light rain and mist, 39°F")
    assert "visibility 1 1/2 miles" in d["summary"]


def test_less_than_quarter_mile_and_freezing_fog():
    d = decode_metar("KMSP 281853Z AUTO 00000KT M1/4SM FZFG VV001 M05/M06 A3020")
    assert d["auto"]
    assert d["visibility"]["text"] == "Less than 1/4 mile"
    assert d["weather"] == ["freezing fog"]
    assert d["temperature"]["temp_c"] == -5
    assert d["temperature"]["temp_f"] == 23
    assert d["sky"] == "Sky obscured"


def test_thunderstorm_with_cumulonimbus():
    d = decode_metar("KMIA 281853Z 18020G35KT 3SM +TSRA BKN025CB OVC050 26/24 A2985")
    assert d["weather"] == ["heavy thunderstorm with rain"]
    assert "cumulonimbus" in d["clouds"][0]["text"]
    assert d["summary"].startswith("Overcast with heavy thunderstorm with rain")


def test_international_metric_metar():
    d = decode_metar("EGLL 281850Z 24008KT 9999 SCT030 BKN045 13/09 Q1013 NOSIG")
    assert d["visibility"]["text"] == "10 km or more (6+ miles)"
    assert d["pressure"]["hpa"] == 1013
    assert d["pressure"]["inhg"] == 29.91
    assert d["sky"] == "Mostly cloudy"
    assert d["unparsed"] == []


def test_cavok():
    d = decode_metar("LFPG 281900Z 05006KT CAVOK 18/10 Q1020")
    assert d["sky"] == "Clear skies"
    assert d["summary"] == "Clear skies, 64°F, winds 7 mph from the northeast."


def test_humidity():
    d = decode_metar("KJFK 280551Z 02004KT 10SM CLR 20/20 A3000")
    assert d["temperature"]["humidity"] == 100


def test_unknown_tokens_are_collected():
    d = decode_metar("KJFK 280551Z 02004KT 10SM CLR 20/10 A3000 XYZZY")
    assert d["unparsed"] == ["XYZZY"]


@pytest.mark.parametrize("deg,expected", [(0, "north"), (90, "east"), (180, "south"), (225, "southwest"), (350, "north")])
def test_compass(deg, expected):
    assert degrees_to_compass(deg) == expected


# --- Flask routes (network call mocked) ---

@pytest.fixture
def client():
    metar_app.app.config["TESTING"] = True
    return metar_app.app.test_client()


def mock_response(monkeypatch, payload, text=None):
    resp = Mock(status_code=200, text=text if text is not None else "x")
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    get = Mock(return_value=resp)
    monkeypatch.setattr(metar_app.requests, "get", get)
    return get


def test_route_success_and_code_normalization(client, monkeypatch):
    get = mock_response(monkeypatch, [{
        "rawOb": "METAR KJFK 280551Z 02004KT 10SM SCT250 07/05 A3043",
        "name": "New York/JF Kennedy Intl, NY, US",
    }])
    resp = client.get("/metar?code=jfk")
    assert resp.status_code == 200
    assert get.call_args.kwargs["params"]["ids"] == "KJFK"
    page = resp.get_data(as_text=True)
    assert "Partly cloudy, 45°F, winds 5 mph from the north-northeast." in page
    assert "JF Kennedy" in page


def test_route_unknown_station(client, monkeypatch):
    mock_response(monkeypatch, [], text="")
    resp = client.get("/metar?code=ZZZZ")
    assert resp.status_code == 404
    assert "No weather report found for ZZZZ" in resp.get_data(as_text=True)


def test_route_invalid_code(client):
    resp = client.get("/metar?code=not-a-code")
    assert resp.status_code == 400


def test_api_route(client, monkeypatch):
    mock_response(monkeypatch, [{"rawOb": "KLAX 281853Z 00000KT 10SM CLR 21/12 A2992", "name": "Los Angeles Intl"}])
    resp = client.get("/api/metar/KLAX")
    assert resp.status_code == 200
    assert resp.get_json()["summary"] == "Clear skies, 70°F, calm winds."
