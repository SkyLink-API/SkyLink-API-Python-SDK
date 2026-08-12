"""``sky.history`` — archived ADS-B, plan-prefixed paths and the positions dispatch.

Fixtures are hand-built from the SQL SELECT lists in
``services/v31/history_service.py`` and the envelopes in
``routers/v31/history_ultra.py`` (see ``fixtures/SOURCES.md``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_API_KEY, TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import NotFoundError
from skylink_api.models.history import (
    HistoryAirportTrafficResponse,
    HistoryFlight,
    HistoryFlightsResponse,
    HistoryPositionsResponse,
    HistoryTrackResponse,
)
from skylink_api.resources.history import (
    AsyncHistory,
    History,
    _airport_traffic_spec,
    _flight_spec,
    _flights_spec,
    _positions_by_icao24_spec,
    _positions_by_registration_spec,
    _positions_spec,
    _track_spec,
    is_icao24,
    resolve_plan,
)

FLIGHT_ID = "7f3c1a54-9d2e-4b8f-a1c6-2e5b7d0f9a13"


@pytest.fixture
def history(client: SkyLink) -> History:
    """The namespace, built directly — wiring onto the client is task A8."""

    return History(client)


@pytest.fixture
def async_history(async_client: AsyncSkyLink) -> AsyncHistory:
    return AsyncHistory(async_client)


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── plan resolution ──────────────────────────────────────────────────────────


def test_resolve_plan_priority() -> None:
    # per-call argument > client config > "ultra"
    assert resolve_plan("mega", "ultra") == "mega"
    assert resolve_plan(None, "mega") == "mega"
    assert resolve_plan(None, None) == "ultra"
    assert resolve_plan("ultra", "mega") == "ultra"


def test_default_plan_is_ultra(history: History, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/ultra/history/flights", load_fixture("history_flights"))

    history.flights(icao24="4ca1fb")

    assert route.calls.last.request.url.path == "/v3.1/ultra/history/flights"


def test_per_call_plan_changes_the_path(history: History, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/mega/history/flights", load_fixture("history_flights"))

    history.flights(icao24="4ca1fb", plan="mega")

    assert route.calls.last.request.url.path == "/v3.1/mega/history/flights"


def test_client_history_plan_is_the_fallback(respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/mega/history/flights", load_fixture("history_flights"))

    with SkyLink(api_key=TEST_API_KEY, history_plan="mega", environ={}) as sky:
        History(sky).flights(callsign="BAW117")

    assert route.calls.last.request.url.path == "/v3.1/mega/history/flights"


def test_per_call_plan_beats_the_client_plan(respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/ultra/history/flights", load_fixture("history_flights"))

    with SkyLink(api_key=TEST_API_KEY, history_plan="mega", environ={}) as sky:
        History(sky).flights(callsign="BAW117", plan="ultra")

    assert route.calls.last.request.url.path == "/v3.1/ultra/history/flights"


def test_every_endpoint_honours_the_plan_prefix() -> None:
    assert _flights_spec(plan="mega", registration="G-STBA").path == "/mega/history/flights"
    assert _flight_spec(FLIGHT_ID, plan="mega").path == f"/mega/history/flight/{FLIGHT_ID}"
    assert _track_spec(FLIGHT_ID, plan="mega").path == f"/mega/history/flight/{FLIGHT_ID}/track"
    assert _positions_by_icao24_spec("4ca1fb", plan="mega").path == "/mega/history/positions/4ca1fb"
    assert (
        _positions_by_registration_spec("G-STBA", plan="mega").path
        == "/mega/history/positions/registration/G-STBA"
    )
    assert _airport_traffic_spec("EGLL", plan="mega").path == "/mega/history/airport/EGLL/traffic"


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_flights_builder_serialises_dates_and_drops_empties() -> None:
    spec = _flights_spec(
        plan="ultra",
        start=datetime(2026, 2, 10, 0, 0, tzinfo=timezone.utc),
        end="2026-02-11T00:00:00Z",
        icao24="4CA1FB",
        limit=10,
    )
    assert spec.method == "GET"
    assert spec.cast_to is HistoryFlightsResponse
    assert spec.query is not None
    # datetimes → ISO 8601; strings pass through untouched.
    assert spec.query["start"] == "2026-02-10T00:00:00+00:00"
    assert spec.query["end"] == "2026-02-11T00:00:00Z"
    # icao24 is lower-cased on the request side (it comes back UPPERCASE).
    assert spec.query["icao24"] == "4ca1fb"
    assert spec.query["limit"] == 10
    # unset filters are None and get dropped by the query builder
    assert spec.query["callsign"] is None
    assert spec.query["registration"] is None

    # Omitted window → no start/end at all, so the API applies its own 24h default.
    bare = _flights_spec(plan="ultra", callsign="BAW117")
    assert bare.query is not None
    assert bare.query["start"] is None
    assert bare.query["end"] is None


def test_positions_builders_and_dispatch() -> None:
    assert is_icao24("4ca1fb") is True
    assert is_icao24("4CA1FB") is True
    assert is_icao24("G-STBA") is False
    assert is_icao24("N12345") is False
    assert is_icao24("4ca1f") is False
    assert is_icao24("4ca1fbb") is False

    # 6 hex → /positions/{icao24}, lower-cased
    assert _positions_spec("4CA1FB", plan="ultra").path == "/ultra/history/positions/4ca1fb"
    # anything else → /positions/registration/{reg}
    assert (
        _positions_spec("G-STBA", plan="ultra").path
        == "/ultra/history/positions/registration/G-STBA"
    )
    # the ambiguous case: a hex-looking tail number is read as an address
    assert _positions_spec("ABC123", plan="ultra").path == "/ultra/history/positions/abc123"

    assert _positions_by_icao24_spec("4ca1fb", plan="ultra").cast_to is HistoryPositionsResponse
    assert _track_spec(FLIGHT_ID, plan="ultra", limit=50).query == {"limit": 50}
    assert _flight_spec(FLIGHT_ID, plan="ultra").cast_to is HistoryFlight


# ── flights ──────────────────────────────────────────────────────────────────


def test_flights_happy_path(history: History, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/ultra/history/flights", load_fixture("history_flights"))

    result = history.flights(
        start=datetime(2026, 2, 10, tzinfo=timezone.utc),
        end=datetime(2026, 2, 12, tzinfo=timezone.utc),
        registration="G-STBA",
        limit=25,
    )

    params = route.calls.last.request.url.params
    assert params["start"] == "2026-02-10T00:00:00+00:00"
    assert params["registration"] == "G-STBA"
    assert params["limit"] == "25"

    assert isinstance(result, HistoryFlightsResponse)
    assert result.note is None
    assert result.count == 2
    assert result.filters is not None
    assert result.filters.resolved_icao24 == "4ca1fb"

    completed, in_progress = result.flights
    assert completed.flight_id == FLIGHT_ID
    # icao24 comes back UPPERCASE in flight rows.
    assert completed.icao24 == "4CA1FB"
    assert completed.flight_number == "BA117"
    assert completed.takeoff_time == datetime(2026, 2, 10, 8, 55, 2, tzinfo=timezone.utc)
    assert completed.flight_duration_min == 444
    assert completed.distance_nm == 2989.4
    assert completed.gps_spoofing_suspected is False
    # Detail-only columns are absent from the search shape.
    assert completed.off_block_time is None
    assert completed.duration_source is None
    assert completed.created_at is None

    # The sparse row parses with the same wide model. Every row is ARCHIVED —
    # history_service.py filters on it, so no other state ever reaches a client.
    assert in_progress.flight_state == "ARCHIVED"
    assert in_progress.callsign is None
    assert in_progress.landing_time is None
    assert in_progress.distance_nm is None


def test_flights_note_is_a_200_not_an_error(history: History, respx_mock: respx.MockRouter) -> None:
    """Unknown registration → 200 with count 0 and an explanatory note."""

    _mock(respx_mock, "/ultra/history/flights", load_fixture("history_empty_note"))

    result = history.flights(registration="G-ZZZZ")

    assert result.count == 0
    assert result.flights == []
    assert result.note is not None
    assert "not found in aircraft database" in result.note
    assert result.filters is not None
    assert result.filters.resolved_icao24 is None


def test_flights_without_a_filter_never_reaches_the_network(
    history: History, respx_mock: respx.MockRouter
) -> None:
    """The API answers 422 to an unfiltered search; the SDK stops it earlier.

    Confirmed live: ``GET /ultra/history/flights`` with no identifier returns
    ``422 At least one of icao24, registration, callsign, departure_icao,
    arrival_icao must be provided``. Refusing client side keeps the quota and
    matches how ``navaids.list`` and ``carbon.estimate`` behave.
    """

    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/ultra/history/flights")

    with pytest.raises(ValueError, match="at least one filter"):
        history.flights()

    assert route.call_count == 0


# ── flight detail ────────────────────────────────────────────────────────────


def test_flight_detail_adds_the_extra_columns(
    history: History, respx_mock: respx.MockRouter
) -> None:
    payload = {
        **load_fixture("history_flights")["flights"][0],
        "off_block_time": "2026-02-10T08:42:11+00:00",
        "on_block_time": "2026-02-10T16:27:53+00:00",
        "duration_source": "adsb",
        "arrival_distance_nm": 2.4,
        "created_at": "2026-02-10T16:30:00+00:00",
        "updated_at": "2026-02-10T16:31:12+00:00",
    }
    route = _mock(respx_mock, f"/ultra/history/flight/{FLIGHT_ID}", payload)

    flight = history.flight(FLIGHT_ID)

    assert route.calls.last.request.url.path == f"/v3.1/ultra/history/flight/{FLIGHT_ID}"
    assert isinstance(flight, HistoryFlight)
    assert flight.duration_source == "adsb"
    assert flight.arrival_distance_nm == 2.4
    assert flight.off_block_time == datetime(2026, 2, 10, 8, 42, 11, tzinfo=timezone.utc)
    assert flight.updated_at == datetime(2026, 2, 10, 16, 31, 12, tzinfo=timezone.utc)


def test_flight_detail_unknown_id_is_a_404(history: History, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/ultra/history/flight/").mock(
        return_value=httpx.Response(404, json={"detail": f"Flight {FLIGHT_ID!r} not found"})
    )

    with pytest.raises(NotFoundError):
        history.flight(FLIGHT_ID)


# ── track ────────────────────────────────────────────────────────────────────


def test_track_positions_use_altitude_baro(history: History, respx_mock: respx.MockRouter) -> None:
    route = _mock(
        respx_mock, f"/ultra/history/flight/{FLIGHT_ID}/track", load_fixture("history_track")
    )

    track = history.track(FLIGHT_ID, limit=500)

    request = route.calls.last.request
    assert request.url.path == f"/v3.1/ultra/history/flight/{FLIGHT_ID}/track"
    assert request.url.params["limit"] == "500"

    assert isinstance(track, HistoryTrackResponse)
    assert track.icao24 == "4CA1FB"
    assert track.registration == "G-STBA"
    assert track.count == 3
    assert track.takeoff_time == datetime(2026, 2, 10, 8, 55, 2, tzinfo=timezone.utc)

    cruise = track.positions[1]
    # The field is altitude_baro here; the live ADS-B feed calls it altitude.
    assert cruise.altitude_baro == 37000
    assert not hasattr(cruise, "altitude")
    assert cruise.is_on_ground is False
    assert cruise.ground_speed == 481.0

    # A position with nothing but a timestamp still parses.
    sparse = track.positions[2]
    assert sparse.altitude_baro is None
    assert sparse.callsign is None
    assert sparse.timestamp == datetime(2026, 2, 10, 8, 55, 5, tzinfo=timezone.utc)
    # newest first
    assert track.positions[0].timestamp is not None
    assert track.positions[0].timestamp > sparse.timestamp


def test_track_limit_is_omitted_when_unset(history: History, respx_mock: respx.MockRouter) -> None:
    route = _mock(
        respx_mock, f"/ultra/history/flight/{FLIGHT_ID}/track", load_fixture("history_track")
    )

    history.track(FLIGHT_ID)

    assert "limit" not in route.calls.last.request.url.params


# ── positions ────────────────────────────────────────────────────────────────

POSITIONS_PAYLOAD: dict[str, Any] = {
    "icao24": "4CA1FB",
    "count": 1,
    "positions": [load_fixture("history_track")["positions"][0]],
    "flights": [
        {
            "flight_id": FLIGHT_ID,
            "callsign": "BAW117",
            "flight_number": "BA117",
            "departure_airport_icao": "EGLL",
            "departure_airport_iata": "LHR",
            "departure_airport_name": "London Heathrow Airport",
            "arrival_airport_icao": "KJFK",
            "arrival_airport_iata": "JFK",
            "arrival_airport_name": "John F Kennedy International Airport",
            "takeoff_time": "2026-02-10T08:55:02+00:00",
            "landing_time": "2026-02-10T16:19:44+00:00",
            "flight_start": "2026-02-10T08:42:11+00:00",
            "flight_end": "2026-02-10T16:27:53+00:00",
            "flight_state": "ARCHIVED",
        }
    ],
}


def test_positions_dispatches_hex_to_the_icao24_route(
    history: History, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/ultra/history/positions/4ca1fb", POSITIONS_PAYLOAD)

    result = history.positions(
        "4CA1FB",
        start="2026-02-10T00:00:00Z",
        end="2026-02-11T00:00:00Z",
        limit=100,
    )

    request = route.calls.last.request
    # requests take lowercase, responses give UPPERCASE
    assert request.url.path == "/v3.1/ultra/history/positions/4ca1fb"
    assert request.url.params["start"] == "2026-02-10T00:00:00Z"
    assert request.url.params["limit"] == "100"

    assert isinstance(result, HistoryPositionsResponse)
    assert result.icao24 == "4CA1FB"
    assert result.registration is None
    assert result.positions[0].altitude_baro == 0
    # Companion flights let position segments be attributed to a flight.
    assert result.flights[0].flight_id == FLIGHT_ID
    assert result.flights[0].flight_state == "ARCHIVED"
    # Narrow row: columns the endpoint does not select stay None.
    assert result.flights[0].icao24 is None
    assert result.flights[0].distance_nm is None


def test_positions_dispatches_a_tail_number_to_the_registration_route(
    history: History, respx_mock: respx.MockRouter
) -> None:
    payload = {**POSITIONS_PAYLOAD, "registration": "GSTBA"}
    route = _mock(respx_mock, "/ultra/history/positions/registration/G-STBA", payload)

    result = history.positions("G-STBA")

    assert route.calls.last.request.url.path == "/v3.1/ultra/history/positions/registration/G-STBA"
    # Only this route echoes a registration, normalised upper case without dashes.
    assert result.registration == "GSTBA"


def test_positions_explicit_variants(history: History, respx_mock: respx.MockRouter) -> None:
    hex_route = _mock(respx_mock, "/ultra/history/positions/4ca1fb", POSITIONS_PAYLOAD)
    reg_route = _mock(
        respx_mock,
        "/mega/history/positions/registration/ABC123",
        {**POSITIONS_PAYLOAD, "registration": "ABC123"},
    )

    history.positions_by_icao24("4ca1fb")
    # The explicit variant forces the registration route for a hex-looking tail.
    result = history.positions_by_registration("ABC123", plan="mega")

    assert hex_route.calls.last.request.url.path == "/v3.1/ultra/history/positions/4ca1fb"
    assert (
        reg_route.calls.last.request.url.path == "/v3.1/mega/history/positions/registration/ABC123"
    )
    assert result.registration == "ABC123"


def test_positions_unknown_registration_is_a_404(
    history: History, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/ultra/history/positions/registration/").mock(
        return_value=httpx.Response(
            404, json={"detail": "Registration 'G-ZZZZ' not found in aircraft database"}
        )
    )

    with pytest.raises(NotFoundError):
        history.positions("G-ZZZZ")


# ── airport traffic ──────────────────────────────────────────────────────────

TRAFFIC_PAYLOAD: dict[str, Any] = {
    "icao": "EGLL",
    "direction": "dep",
    "count": 1,
    "flights": [
        {
            "flight_id": FLIGHT_ID,
            "icao24": "4CA1FB",
            "callsign": "BAW117",
            "flight_number": "BA117",
            "aircraft_type_icao": "B77W",
            "airline_name": "British Airways",
            "departure_airport_icao": "EGLL",
            "departure_airport_iata": "LHR",
            "arrival_airport_icao": "KJFK",
            "arrival_airport_iata": "JFK",
            "flight_state": "ARCHIVED",
            "takeoff_time": "2026-02-10T08:55:02+00:00",
            "landing_time": "2026-02-10T16:19:44+00:00",
            "flight_duration_min": 444,
            "distance_nm": 2989.4,
        }
    ],
}


def test_airport_traffic_defaults_to_both(history: History, respx_mock: respx.MockRouter) -> None:
    route = _mock(
        respx_mock, "/ultra/history/airport/EGLL/traffic", {**TRAFFIC_PAYLOAD, "direction": "both"}
    )

    result = history.airport_traffic("EGLL")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/ultra/history/airport/EGLL/traffic"
    assert request.url.params["direction"] == "both"
    assert "start" not in request.url.params

    assert isinstance(result, HistoryAirportTrafficResponse)
    assert result.direction == "both"
    assert result.flights[0].airline_name == "British Airways"
    # Traffic rows carry no airline codes or aircraft type name.
    assert result.flights[0].airline_icao is None
    assert result.flights[0].aircraft_type_name is None


def test_airport_traffic_direction_and_window(
    history: History, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/mega/history/airport/EGLL/traffic", TRAFFIC_PAYLOAD)

    result = history.airport_traffic(
        "EGLL",
        direction="dep",
        start=datetime(2026, 2, 1, tzinfo=timezone.utc),
        end=datetime(2026, 2, 11, tzinfo=timezone.utc),
        limit=200,
        plan="mega",
    )

    params = route.calls.last.request.url.params
    assert params["direction"] == "dep"
    assert params["start"] == "2026-02-01T00:00:00+00:00"
    assert params["end"] == "2026-02-11T00:00:00+00:00"
    assert params["limit"] == "200"
    assert result.count == 1


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_flights_and_plan_override(
    async_history: AsyncHistory, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/mega/history/flights", load_fixture("history_flights"))

    result = await async_history.flights(icao24="4CA1FB", plan="mega")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/mega/history/flights"
    assert request.url.params["icao24"] == "4ca1fb"
    assert result.count == 2
    assert result.flights[0].icao24 == "4CA1FB"


async def test_async_track_and_positions_dispatch(
    async_history: AsyncHistory, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, f"/ultra/history/flight/{FLIGHT_ID}/track", load_fixture("history_track"))
    _mock(respx_mock, "/ultra/history/positions/registration/G-STBA", POSITIONS_PAYLOAD)

    track = await async_history.track(FLIGHT_ID)
    positions = await async_history.positions("G-STBA")

    assert track.positions[1].altitude_baro == 37000
    assert positions.icao24 == "4CA1FB"
