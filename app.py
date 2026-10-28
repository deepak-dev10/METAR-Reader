"""METAR Reader: a Flask app that turns airport weather reports into plain English.

The user enters an airport code, the app fetches the latest METAR from the
aviationweather.gov API, decodes it with :mod:`metar_decoder`, and shows a
friendly summary such as "Partly cloudy, 45°F, winds 5 mph from the north."

Routes:
    GET /                   Search page.
    GET /metar?code=<code>  Search results page for an airport.
    GET /api/metar/<code>   Decoded report as JSON.

Run locally with::

    flask --app app run
"""

import re

import requests
from flask import Flask, jsonify, render_template, request

from metar_decoder import decode_metar

API_URL = "https://aviationweather.gov/api/data/metar/"

# Airport codes are 3 (IATA, e.g. JFK) or 4 (ICAO, e.g. KJFK) characters.
# Some small airfields use digits, e.g. 1O2.
CODE_RE = re.compile(r"^[A-Z0-9]{3,4}$")

app = Flask(__name__)


class MetarError(Exception):
    """An error that is safe to show to the user.

    Attributes:
        message: Friendly explanation to display on the page or in JSON.
        status: HTTP status code to return with the error.
    """

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def normalize_code(code):
    """Clean up and validate an airport code entered by the user.

    The aviationweather.gov API expects 4-letter ICAO codes. Three-letter
    codes are assumed to be US airports and are prefixed with ``K``
    (JFK -> KJFK), which is how US ICAO codes are formed.

    Args:
        code: Raw user input, possibly lower case or padded with spaces.

    Returns:
        The upper-case ICAO code.

    Raises:
        MetarError: If the input is not a 3 or 4 character code.
    """
    code = (code or "").strip().upper()
    if not CODE_RE.match(code):
        raise MetarError("Please enter a 3 or 4 character airport code, like KJFK or EGLL.")
    # Only expand all-letter codes; codes with digits are local identifiers.
    if len(code) == 3 and code.isalpha():
        code = "K" + code
    return code


def fetch_metar(code):
    """Fetch the latest observation for a station from aviationweather.gov.

    We request the JSON format because it includes the airport's full name
    alongside the raw METAR text (``rawOb``).

    Args:
        code: A validated ICAO airport code.

    Returns:
        The API's observation dict for the station.

    Raises:
        MetarError: If the API cannot be reached, returns invalid data, or
            has no report for the station.
    """
    try:
        resp = requests.get(
            API_URL,
            params={"ids": code, "format": "json"},
            headers={"User-Agent": "METAR-Reader/1.0"},
            timeout=10,  # don't leave the user waiting if the API is slow
        )
        resp.raise_for_status()
    except requests.RequestException:
        raise MetarError("Couldn't reach the aviation weather service. Please try again in a moment.", 502)

    # Unknown stations come back as an empty body (HTTP 204) or an empty list.
    if not resp.text.strip():
        data = []
    else:
        try:
            data = resp.json()
        except ValueError:
            raise MetarError("The aviation weather service sent back an unexpected response.", 502)

    if not data or not data[0].get("rawOb"):
        raise MetarError(f"No weather report found for {code}. Check the airport code and try again.", 404)
    return data[0]


def get_report(code):
    """Validate a code, fetch its METAR and decode it.

    Args:
        code: Airport code as entered by the user.

    Returns:
        The decoded report from :func:`metar_decoder.decode_metar`, with an
        extra ``name`` key holding the airport's full name.

    Raises:
        MetarError: If the code is invalid or no report can be fetched.
    """
    code = normalize_code(code)
    observation = fetch_metar(code)
    report = decode_metar(observation["rawOb"])
    report["name"] = observation.get("name") or code
    return report


@app.route("/")
def index():
    """Show the search page."""
    return render_template("index.html")


@app.route("/metar")
def metar():
    """Show the decoded weather for the airport in the ``code`` query parameter.

    Errors are shown on the same page with a suitable HTTP status code.
    """
    code = request.args.get("code", "")
    try:
        report = get_report(code)
    except MetarError as e:
        return render_template("index.html", code=code, error=e.message), e.status
    return render_template("index.html", code=code, report=report)


@app.route("/api/metar/<code>")
def metar_api(code):
    """Return the decoded weather report as JSON.

    Errors are returned as ``{"error": "<message>"}`` with a suitable
    HTTP status code.
    """
    try:
        return jsonify(get_report(code))
    except MetarError as e:
        return jsonify({"error": e.message}), e.status


if __name__ == "__main__":
    # Debug mode is for local development only. Use a production WSGI
    # server (e.g. gunicorn) when deploying.
    app.run(debug=True)
