"""Shared pytest fixtures for the METAR Reader tests."""

import json
from unittest.mock import Mock

import pytest
import requests

import app as app_module


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Fail any test that tries to call the real aviationweather.gov API.

    Tests that need an API response should use the ``mock_api`` fixture,
    which replaces this guard with a fake response.
    """
    guard = Mock(side_effect=AssertionError("Unexpected real network call in a test"))
    monkeypatch.setattr(app_module.requests, "get", guard)


@pytest.fixture
def client():
    """A Flask test client for making requests to the app."""
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


@pytest.fixture
def mock_api(monkeypatch):
    """Factory that fakes the aviationweather.gov API response.

    Usage::

        get = mock_api([{"rawOb": "KJFK ...", "name": "JFK"}])  # JSON payload
        get = mock_api(text="", status_code=204)                # empty body
        get = mock_api(json_error=True)                          # invalid JSON
        get = mock_api(status_code=500)                          # HTTP error
        get = mock_api(exc=requests.Timeout())                   # network failure

    Returns:
        The ``Mock`` standing in for ``requests.get``, so tests can check
        how the API was called.
    """
    def _mock(payload=None, *, text=None, status_code=200, json_error=False, exc=None):
        if exc is not None:
            get = Mock(side_effect=exc)
        else:
            resp = Mock(status_code=status_code)
            resp.text = text if text is not None else json.dumps(payload)
            if json_error:
                resp.json.side_effect = ValueError("Invalid JSON")
            else:
                resp.json.return_value = payload
            if status_code >= 400:
                resp.raise_for_status.side_effect = requests.HTTPError(f"{status_code} Server Error")
            else:
                resp.raise_for_status.return_value = None
            get = Mock(return_value=resp)
        monkeypatch.setattr(app_module.requests, "get", get)
        return get

    return _mock
