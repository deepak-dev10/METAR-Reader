"""Unit tests for the Flask app.

The aviationweather.gov API is never called for real: the ``mock_api``
fixture (see conftest.py) fakes its responses, so these tests run offline
and always see the same mock METAR readings.
"""

import pytest
import requests

from app import API_URL, MetarError, fetch_metar, get_report, normalize_code

SEATTLE_RAW = "SPECI KSEA 280708Z 17012KT 10SM -RA SCT017 BKN025 OVC038 08/07 A2988 RMK AO2"
SEATTLE_NAME = "Seattle-Tacoma Intl, WA, US"
SEATTLE_SUMMARY = "Overcast with light rain, 46°F, winds 14 mph from the south."


def observation(raw=SEATTLE_RAW, name=SEATTLE_NAME):
    """Build a mock API response body containing one observation."""
    obs = {"icaoId": raw.split()[1] if raw.startswith(("METAR", "SPECI")) else raw.split()[0], "rawOb": raw}
    if name is not None:
        obs["name"] = name
    return [obs]


# --- normalize_code ---------------------------------------------------------

@pytest.mark.parametrize("user_input, expected", [
    ("KJFK", "KJFK"),
    ("kjfk", "KJFK"),        # lower case
    ("  jfk  ", "KJFK"),     # spaces trimmed, 3-letter code expanded
    ("LAX", "KLAX"),
    ("EGLL", "EGLL"),        # international ICAO code unchanged
    ("1O2", "1O2"),          # codes with digits are not expanded
])
def test_normalize_code_valid(user_input, expected):
    assert normalize_code(user_input) == expected


@pytest.mark.parametrize("user_input", ["", "   ", None, "AB", "ABCDE", "K-FK", "<script>"])
def test_normalize_code_invalid(user_input):
    with pytest.raises(MetarError) as excinfo:
        normalize_code(user_input)
    assert excinfo.value.status == 400
    assert "3 or 4 character airport code" in excinfo.value.message


# --- fetch_metar ------------------------------------------------------------

def test_fetch_metar_calls_api_correctly(mock_api):
    get = mock_api(observation())
    fetch_metar("KSEA")
    get.assert_called_once()
    args, kwargs = get.call_args
    assert args == (API_URL,)
    assert kwargs["params"] == {"ids": "KSEA", "format": "json"}
    assert "User-Agent" in kwargs["headers"]
    assert kwargs["timeout"] == 10


def test_fetch_metar_returns_first_observation(mock_api):
    mock_api(observation() + observation("KJFK 280551Z 02004KT", "JFK"))
    assert fetch_metar("KSEA")["rawOb"] == SEATTLE_RAW


@pytest.mark.parametrize("kwargs", [
    pytest.param({"text": "", "status_code": 204}, id="empty-body"),
    pytest.param({"payload": []}, id="empty-list"),
    pytest.param({"payload": [{"icaoId": "KSEA", "name": "Seattle"}]}, id="missing-rawOb"),
])
def test_fetch_metar_no_report_is_404(mock_api, kwargs):
    mock_api(**kwargs)
    with pytest.raises(MetarError) as excinfo:
        fetch_metar("ZZZZ")
    assert excinfo.value.status == 404
    assert "No weather report found for ZZZZ" in excinfo.value.message


@pytest.mark.parametrize("kwargs", [
    pytest.param({"exc": requests.Timeout()}, id="timeout"),
    pytest.param({"exc": requests.ConnectionError()}, id="connection-error"),
    pytest.param({"status_code": 500, "text": "Server error"}, id="http-500"),
])
def test_fetch_metar_service_unavailable_is_502(mock_api, kwargs):
    mock_api(**kwargs)
    with pytest.raises(MetarError) as excinfo:
        fetch_metar("KSEA")
    assert excinfo.value.status == 502
    assert "Couldn't reach" in excinfo.value.message


def test_fetch_metar_invalid_json_is_502(mock_api):
    mock_api(text="<html>oops</html>", json_error=True)
    with pytest.raises(MetarError) as excinfo:
        fetch_metar("KSEA")
    assert excinfo.value.status == 502
    assert "unexpected response" in excinfo.value.message


# --- get_report -------------------------------------------------------------

def test_get_report_decodes_and_adds_name(mock_api):
    get = mock_api(observation())
    report = get_report("ksea")
    assert get.call_args.kwargs["params"]["ids"] == "KSEA"
    assert report["name"] == SEATTLE_NAME
    assert report["station"] == "KSEA"
    assert report["summary"] == SEATTLE_SUMMARY


def test_get_report_falls_back_to_code_when_name_missing(mock_api):
    mock_api(observation(name=None))
    assert get_report("KSEA")["name"] == "KSEA"


def test_get_report_invalid_code_does_not_call_api(mock_api):
    get = mock_api(observation())
    with pytest.raises(MetarError):
        get_report("not-a-code")
    get.assert_not_called()


# --- GET / ------------------------------------------------------------------

def test_index_shows_search_form(client):
    resp = client.get("/")
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert '<form class="search"' in page
    assert 'name="code"' in page
    assert 'class="card"' not in page  # no result yet


# --- GET /metar -------------------------------------------------------------

def test_metar_page_shows_decoded_report(client, mock_api):
    mock_api(observation())
    resp = client.get("/metar?code=ksea")
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert SEATTLE_SUMMARY in page
    assert SEATTLE_NAME in page
    assert SEATTLE_RAW in page                    # raw METAR in the collapsible section
    assert "46°F (8°C)" in page                   # temperature
    assert "93%" in page                          # humidity
    assert "14 mph from the south (170°)" in page  # wind
    assert "Overcast at 3,800 ft" in page         # cloud layer
    assert "Light rain" in page                   # weather
    assert "29.88 inHg (1012 hPa)" in page        # pressure
    assert "The 28th at 07:08 UTC" in page        # observation time


def test_metar_page_shows_wind_variation_and_auto_station(client, mock_api):
    mock_api(observation("METAR KDEN 281853Z AUTO 27015G28KT 240V300 10SM FEW080 15/M02 A3001", "Denver Intl"))
    page = client.get("/metar?code=KDEN").get_data(as_text=True)
    assert "Varying between west-southwest (240°) and west-northwest (300°)" in page
    assert "Automated station" in page


@pytest.mark.parametrize("query, status, message", [
    ("", 400, "3 or 4 character airport code"),
    ("?code=", 400, "3 or 4 character airport code"),
    ("?code=AB", 400, "3 or 4 character airport code"),
])
def test_metar_page_invalid_code(client, query, status, message):
    resp = client.get(f"/metar{query}")
    assert resp.status_code == status
    assert message in resp.get_data(as_text=True)


def test_metar_page_unknown_airport(client, mock_api):
    mock_api([])
    resp = client.get("/metar?code=ZZZZ")
    assert resp.status_code == 404
    assert "No weather report found for ZZZZ" in resp.get_data(as_text=True)


def test_metar_page_service_down(client, mock_api):
    mock_api(exc=requests.Timeout())
    resp = client.get("/metar?code=KSEA")
    assert resp.status_code == 502
    assert "Couldn&#39;t reach the aviation weather service" in resp.get_data(as_text=True)


def test_metar_page_escapes_user_input(client):
    resp = client.get("/metar?code=<script>alert(1)</script>")
    page = resp.get_data(as_text=True)
    assert resp.status_code == 400
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# --- GET /api/metar/<code> --------------------------------------------------

def test_api_returns_decoded_json(client, mock_api):
    mock_api(observation())
    resp = client.get("/api/metar/ksea")
    data = resp.get_json()
    assert resp.status_code == 200
    assert resp.content_type == "application/json"
    assert data["station"] == "KSEA"
    assert data["name"] == SEATTLE_NAME
    assert data["summary"] == SEATTLE_SUMMARY
    assert data["wind"]["speed_mph"] == 14
    assert data["temperature"]["temp_f"] == 46
    assert data["weather"] == ["light rain"]
    assert len(data["clouds"]) == 3


@pytest.mark.parametrize("code, api_kwargs, status", [
    pytest.param("AB", None, 400, id="invalid-code"),
    pytest.param("ZZZZ", {"payload": []}, 404, id="unknown-airport"),
    pytest.param("KSEA", {"exc": requests.ConnectionError()}, 502, id="service-down"),
])
def test_api_errors_return_json(client, mock_api, code, api_kwargs, status):
    if api_kwargs is not None:
        mock_api(**api_kwargs)
    resp = client.get(f"/api/metar/{code}")
    assert resp.status_code == status
    assert set(resp.get_json()) == {"error"}
