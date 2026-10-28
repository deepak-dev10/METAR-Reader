# METAR Reader

A small Flask app that turns cryptic METAR aviation weather reports into plain English.

Type an airport code (like `KJFK`, `LAX`, or `EGLL`) and get back something like:

> Partly cloudy, 45°F, winds 5 mph from the north-northeast.

You also get a details panel (temperature, dewpoint, humidity, wind, visibility, clouds, pressure) and the raw METAR.

Data comes from the [aviationweather.gov API](https://aviationweather.gov/api/data/metar/?ids=KJFK).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
flask --app app run
```

Then open http://127.0.0.1:5000.

There's also a JSON endpoint: `GET /api/metar/<code>`.

## Test

```bash
pytest
```

## Project layout

- `app.py`: Flask routes and the call to aviationweather.gov
- `metar_decoder.py`: the METAR parser and plain-English summary
- `templates/`, `static/`: the web page
- `tests/`: decoder and route tests
