"""METAR Reader — type an airport code, get a plain-English weather report."""

import re

import requests
from flask import Flask, jsonify, render_template, request

from metar_decoder import decode_metar

API_URL = "https://aviationweather.gov/api/data/metar/"
CODE_RE = re.compile(r"^[A-Z0-9]{3,4}$")

app = Flask(__name__)


class MetarError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def normalize_code(code):
    """Uppercase the code and turn 3-letter US codes (JFK) into ICAO codes (KJFK)."""
    code = (code or "").strip().upper()
    if not CODE_RE.match(code):
        raise MetarError("Please enter a 3 or 4 character airport code, like KJFK or EGLL.")
    if len(code) == 3 and code.isalpha():
        code = "K" + code
    return code


def fetch_metar(code):
    """Fetch the latest observation for a station from aviationweather.gov."""
    try:
        resp = requests.get(
            API_URL,
            params={"ids": code, "format": "json"},
            headers={"User-Agent": "METAR-Reader/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException:
        raise MetarError("Couldn't reach the aviation weather service. Please try again in a moment.", 502)

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
    code = normalize_code(code)
    observation = fetch_metar(code)
    report = decode_metar(observation["rawOb"])
    report["name"] = observation.get("name") or code
    return report


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/metar")
def metar():
    code = request.args.get("code", "")
    try:
        report = get_report(code)
    except MetarError as e:
        return render_template("index.html", code=code, error=e.message), e.status
    return render_template("index.html", code=code, report=report)


@app.route("/api/metar/<code>")
def metar_api(code):
    try:
        return jsonify(get_report(code))
    except MetarError as e:
        return jsonify({"error": e.message}), e.status


if __name__ == "__main__":
    app.run(debug=True)
