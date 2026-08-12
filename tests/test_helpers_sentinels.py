"""HTTP-200 sentinels turned into exceptions (helpers §6).

All three sentinels come straight from their fixtures, so the payloads are the
ones the backend really sends: ``found:false``, ``count:0 + note``, and the IP
search that reports a geolocation failure inside a 200.
"""

from __future__ import annotations

from typing import Any

import pytest

from skylink_api import NotFoundError, SkyLinkError
from skylink_api.helpers.sentinels import (
    has_results,
    is_found,
    require_found,
    require_ip_result,
    require_results,
)
from skylink_api.models.aircraft import AircraftLookup
from skylink_api.models.airports import AirportsByIPResponse
from skylink_api.models.history import HistoryFlightsResponse, HistoryTrackResponse


@pytest.fixture
def found(fixture: Any) -> AircraftLookup:
    return AircraftLookup.model_validate(fixture("aircraft_found"))


@pytest.fixture
def missing(fixture: Any) -> AircraftLookup:
    return AircraftLookup.model_validate(fixture("aircraft_not_found"))


# ── aircraft lookup ──────────────────────────────────────────────────────────


def test_is_found_narrows_a_hit(found: AircraftLookup, missing: AircraftLookup) -> None:
    assert is_found(found) is True
    assert is_found(missing) is False


def test_is_found_rejects_a_hit_without_a_body() -> None:
    """``found:true`` with ``aircraft:null`` would defeat the point of narrowing."""

    assert is_found(AircraftLookup(query="G-STBA", found=True, aircraft=None)) is False


def test_require_found_returns_the_same_object(found: AircraftLookup) -> None:
    result = require_found(found)
    assert result is found
    assert result.aircraft.registration == "G-STBA"


def test_require_found_raises_not_found_quoting_the_query(missing: AircraftLookup) -> None:
    """The registry never 404s — it answers 200 with ``found:false``."""

    with pytest.raises(NotFoundError) as error:
        require_found(missing)
    assert "ZZ-ZZZ" in str(error.value)
    assert error.value.status_code == 404


# ── history ──────────────────────────────────────────────────────────────────


def test_require_results_passes_a_populated_response_through(fixture: Any) -> None:
    response = HistoryFlightsResponse.model_validate(fixture("history_flights"))
    assert has_results(response) is True
    assert require_results(response) is response


def test_require_results_quotes_the_note(fixture: Any) -> None:
    """``count:0`` plus a ``note`` means "unknown registration", not "never flew"."""

    response = HistoryFlightsResponse.model_validate(fixture("history_empty_note"))
    assert has_results(response) is False
    with pytest.raises(NotFoundError) as error:
        require_results(response)
    assert "not found in aircraft database" in str(error.value)
    assert "G-ZZZZ" in str(error.value)


def test_require_results_without_a_note() -> None:
    with pytest.raises(NotFoundError, match="count=0"):
        require_results(HistoryFlightsResponse(count=0, flights=[]))


def test_require_results_works_on_a_track(fixture: Any) -> None:
    track = HistoryTrackResponse.model_validate(fixture("history_track"))
    assert require_results(track) is track
    with pytest.raises(NotFoundError):
        require_results(HistoryTrackResponse(flight_id="x", count=0, positions=[]))


def test_require_results_accepts_a_dict() -> None:
    assert require_results({"count": 2, "flights": [{}, {}]})["count"] == 2
    with pytest.raises(NotFoundError):
        require_results({"count": 0, "flights": []})


# ── IP search ────────────────────────────────────────────────────────────────


def test_require_ip_result_raises_on_the_error_sentinel(fixture: Any) -> None:
    """A geolocation failure arrives as a 200 with ``error`` set."""

    response = AirportsByIPResponse.model_validate(fixture("airports_ip_error"))
    with pytest.raises(SkyLinkError) as error:
        require_ip_result(response)
    assert "Could not geolocate" in str(error.value)
    assert "10.0.0.1" in str(error.value)


def test_require_ip_result_passes_a_good_response_through() -> None:
    response = AirportsByIPResponse(
        ip_address="8.8.8.8",
        location=None,
        airports=[],
        airports_found=0,
        error=None,
    )
    assert require_ip_result(response) is response
