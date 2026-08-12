"""``sky.compose`` — multi-endpoint aggregates (contract §9).

Three things are worth testing here and they are all about *behaviour under
partial failure and under load*, not about payload parsing (the per-namespace
suites already cover that):

* **which** requests go out for a given ``include``/``exclude``;
* **where the failures land** — ``errors[part]`` for everything except the
  primary request, which is raised;
* **that the sub-requests are actually parallel**. Two tests assert it directly
  (``*_really_runs_in_parallel``): a handler that counts simultaneous requests
  proves overlap, and the wall clock proves the parts do not add up.

The pure helpers are exercised without a client at the top of the file.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import (
    InternalServerError,
    NotFoundError,
    ServiceUnavailableError,
    SkyLinkError,
)
from skylink_api.models.aircraft import AircraftLookup
from skylink_api.models.airports import EnrichedAirport
from skylink_api.models.carbon import CarbonEstimate
from skylink_api.models.charts import ChartsResponse
from skylink_api.models.compose import (
    AirportBrief,
    EnrichedAircraft,
    FlightBrief,
    RouteBrief,
    ScheduleWithStatus,
)
from skylink_api.models.delays import FaaDelayResponse
from skylink_api.models.distance import DistanceResponse
from skylink_api.models.flight_status import FlightStatusResponse
from skylink_api.models.geo import Country
from skylink_api.models.ml import FlightTimePrediction
from skylink_api.models.notams import NotamsResponse
from skylink_api.models.routes import AirlineRoutesResult, VrsRouteResult
from skylink_api.models.schedules import ArrivalsResponse, DeparturesResponse
from skylink_api.models.weather import MetarWithParsed, TafWithParsed
from skylink_api.resources.compose import (
    AIRPORT_BRIEF_PARTS,
    FLIGHT_BRIEF_PARTS,
    ROUTE_BRIEF_PARTS,
    AsyncCompose,
    Compose,
    _check_count,
    _flight_carbon_spec,
    _is_north_american,
    _lookup_plan,
    _normalize_flight_number,
    _registration_of,
    _select_parts,
    _truncate_board,
)

ICAO = "EGLL"

DELAYS_PAYLOAD: dict[str, Any] = {
    "ground_delays": [],
    "ground_stops": [],
    "closures": [],
    "airspace_flow_programs": [],
    "total_alerts": 0,
    "updated": "2026-02-11T12:00:00Z",
}

DISTANCE_PAYLOAD: dict[str, Any] = {
    "from_point": {"latitude": 51.47, "longitude": -0.46, "icao_code": "EGLL"},
    "to_point": {"latitude": 40.64, "longitude": -73.78, "icao_code": "KJFK"},
    "distance": 2991.01,
    "unit": "nm",
    "bearing": 288.4,
    "bearing_cardinal": "WNW",
}

FLIGHT_TIME_PAYLOAD: dict[str, Any] = {
    "origin": "EGLL",
    "destination": "KJFK",
    "aircraft_type": "B77W",
    "distance_nm": 2991.01,
    "estimated_minutes": 443,
    "estimated_hours_display": "7h 23m",
    "min_minutes": 415,
    "max_minutes": 470,
    "model_version": "flight_time_v2",
}

ARRIVALS_PAYLOAD: dict[str, Any] = {
    "iata": "LHR",
    "direction": "arrivals",
    "airport_code": "EGLL",
    "flights": [
        {
            "Time": "16:20",
            "Date": "11 Feb",
            "IATA": "MAD",
            "Origin": "Madrid",
            "Flight": "IB3160",
            "Airline": "Iberia",
            "Status": "Landed 16:15",
        }
    ],
    "total_flights": 91,
    "pages_fetched": 4,
}

COUNTRIES_PAYLOAD: dict[str, Any] = {
    "countries": [
        {"id": 1, "code": "US", "name": "United States", "continent": None},
        {"id": 2, "code": "GB", "name": "United Kingdom", "continent": "EU"},
        {"id": 3, "code": "CA", "name": "Canada", "continent": ""},
        {"id": 4, "code": "MX", "name": "Mexico", "continent": "NA"},
        {"id": 5, "code": "BR", "name": "Brazil", "continent": "SA"},
    ],
    "total": 5,
}


@pytest.fixture
def compose(client: SkyLink) -> Compose:
    return Compose(client)


@pytest.fixture
def async_compose(async_client: AsyncSkyLink) -> AsyncCompose:
    return AsyncCompose(async_client)


def _adsb_row(icao24: str | None, callsign: str | None = None) -> Any:
    from skylink_api.models.adsb import AdsbAircraft

    return AdsbAircraft.model_validate({"icao24": icao24, "callsign": callsign})


def _status_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = dict(load_fixture("flight_status"))
    payload.update(overrides)
    return payload


def _mock_airport_brief(respx_mock: respx.MockRouter) -> dict[str, respx.Route]:
    """Every part of an airport brief, answering 200."""

    def ok(path: str, payload: Any) -> respx.Route:
        return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
            return_value=httpx.Response(200, json=payload)
        )

    return {
        "airport": ok("/airports/search", load_fixture("airports_search")),
        "metar": ok(f"/weather/metar/{ICAO}", load_fixture("weather_metar_parsed")),
        "taf": ok(f"/weather/taf/{ICAO}", load_fixture("weather_taf_parsed")),
        "notams": ok(f"/notams/{ICAO}", load_fixture("notams")),
        "delays": ok(f"/delays/faa/{ICAO}", DELAYS_PAYLOAD),
        "charts": ok(f"/charts/{ICAO}", load_fixture("charts")),
        "departures": ok("/schedules/departures", load_fixture("schedules_departures")),
        "arrivals": ok("/schedules/arrivals", ARRIVALS_PAYLOAD),
    }


def _mock_route_brief(respx_mock: respx.MockRouter) -> dict[str, respx.Route]:
    def ok(path: str, payload: Any) -> respx.Route:
        return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
            return_value=httpx.Response(200, json=payload)
        )

    metar = load_fixture("weather_metar_parsed")
    taf = load_fixture("weather_taf_parsed")
    return {
        "distance": ok("/distance", DISTANCE_PAYLOAD),
        "flight_time": ok("/ml/flight-time", FLIGHT_TIME_PAYLOAD),
        "origin_metar": ok("/weather/metar/EGLL", metar),
        "destination_metar": ok("/weather/metar/KJFK", metar),
        "origin_taf": ok("/weather/taf/EGLL", taf),
        "destination_taf": ok("/weather/taf/KJFK", taf),
        "carbon": ok("/carbon/estimate", load_fixture("carbon")),
    }


class _ConcurrencyProbe:
    """A respx handler that records how many requests were in flight at once.

    The point of ``compose`` is that the parts overlap. ``max_active`` proves
    they do; the elapsed time proves they do not add up.
    """

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def handler(self, payload: Any) -> Any:
        def respond(request: httpx.Request) -> httpx.Response:
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(self.delay)
            with self._lock:
                self.active -= 1
            return httpx.Response(200, json=payload)

        return respond

    def async_handler(self, payload: Any) -> Any:
        async def respond(request: httpx.Request) -> httpx.Response:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(self.delay)
            self.active -= 1
            return httpx.Response(200, json=payload)

        return respond


# ── wiring ───────────────────────────────────────────────────────────────────


def test_compose_is_attached_to_both_clients(client: SkyLink, async_client: AsyncSkyLink) -> None:
    assert isinstance(client.compose, Compose)
    assert type(async_client.compose) is AsyncCompose
    assert client.compose is client.compose  # cached_property
    assert client.compose._client is client


def test_part_constants_match_the_result_fields() -> None:
    """The part names are the dataclass field names — that is the whole contract."""

    assert set(AIRPORT_BRIEF_PARTS) | {"errors"} == set(AirportBrief.__dataclass_fields__)
    assert set(FLIGHT_BRIEF_PARTS) | {"errors"} == set(FlightBrief.__dataclass_fields__)
    assert set(ROUTE_BRIEF_PARTS) | {"errors"} == set(RouteBrief.__dataclass_fields__)


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_select_parts_defaults_to_everything() -> None:
    assert _select_parts(AIRPORT_BRIEF_PARTS, None, None, caller="x") == AIRPORT_BRIEF_PARTS


def test_select_parts_include_and_exclude_keep_canonical_order() -> None:
    assert _select_parts(AIRPORT_BRIEF_PARTS, ("taf", "metar", "taf"), None, caller="x") == (
        "metar",
        "taf",
    )
    assert _select_parts(AIRPORT_BRIEF_PARTS, None, ("charts", "delays"), caller="x") == (
        "airport",
        "metar",
        "taf",
        "notams",
        "departures",
        "arrivals",
    )


def test_select_parts_refuses_both_selectors() -> None:
    with pytest.raises(ValueError, match="not both"):
        _select_parts(AIRPORT_BRIEF_PARTS, ("metar",), ("taf",), caller="compose.airport_brief()")


@pytest.mark.parametrize("kwargs", [{"include": ("mtear",)}, {"exclude": ("weather",)}])
def test_select_parts_lists_the_valid_names_on_a_typo(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError) as excinfo:
        _select_parts(
            AIRPORT_BRIEF_PARTS,
            kwargs.get("include"),
            kwargs.get("exclude"),
            caller="compose.airport_brief()",
        )

    message = str(excinfo.value)
    assert "valid parts are" in message
    assert "departures" in message


@pytest.mark.parametrize("value", [-1, "10", None, float("nan"), True])
def test_check_count_rejects_the_unusable(value: Any) -> None:
    with pytest.raises(ValueError):
        _check_count(value, "limit")


@pytest.mark.parametrize("value", [1.5, 10.0, float("inf")])
def test_check_count_requires_a_whole_number(value: Any) -> None:
    """A fractional row count is a mistake; truncating it would hide the mistake.

    Same rule as the TypeScript SDK's ``requireCount`` (``Number.isInteger``).
    """

    with pytest.raises(ValueError, match="must be an integer >= 0"):
        _check_count(value, "limit")


def test_check_count_allows_zero() -> None:
    assert _check_count(0, "limit") == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("BA 123", "BA123"),
        ("ba123", "BA123"),
        (" BAW 117 ", "BAW117"),
        (None, None),
        ("", None),
        ("--", None),
    ],
)
def test_normalize_flight_number(value: str | None, expected: str | None) -> None:
    assert _normalize_flight_number(value) == expected


def test_registration_of_the_live_payload_is_none() -> None:
    """The live status carries no airframe — the airframe part is skipped, not failed."""

    status = FlightStatusResponse.model_validate(load_fixture("flight_status"))

    assert _registration_of(status) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"registration": "g-stba"},
        {"tail_number": " G-STBA "},
        {"aircraft": {"registration": "g-stba"}},
    ],
)
def test_registration_of_finds_extra_fields(payload: dict[str, Any]) -> None:
    """``extra="allow"`` means a registration the backend adds later is usable."""

    status = FlightStatusResponse.model_validate(_status_payload(**payload))

    assert _registration_of(status) == "G-STBA"


@pytest.mark.parametrize(
    "payload",
    [
        {"registration": "--"},
        {"registration": "-"},
        {"registration": "N/A"},
        {"registration": "n/a"},
        {"registration": "   "},
        {"registration": "unknown"},
        {"aircraft": {"registration": "--"}},
    ],
)
def test_registration_of_ignores_scraper_placeholders(payload: dict[str, Any]) -> None:
    """``format_flightera_output`` writes ``"--"``/``"N/A"`` for "no value".

    Taking one at face value would spend a guaranteed-404 registry lookup on it.
    """

    status = FlightStatusResponse.model_validate(_status_payload(**payload))

    assert _registration_of(status) is None


def test_registration_of_skips_a_placeholder_for_a_real_value() -> None:
    """A placeholder under one spelling must not mask a tail number under another."""

    status = FlightStatusResponse.model_validate(
        _status_payload(registration="--", tail_number="g-stba")
    )

    assert _registration_of(status) == "G-STBA"


def test_flight_carbon_spec_prefers_the_vrs_airport_pair() -> None:
    route = VrsRouteResult.model_validate(load_fixture("routes_vrs"))

    spec = _flight_carbon_spec(route, callsign="BAW117", aircraft_type="B77W")

    assert spec is not None
    assert spec.query == {
        "departure_icao": "EGLL",
        "arrival_icao": "KJFK",
        "callsign": None,
        "aircraft_type": "B77W",
        "passengers": None,
        "include_rfi": False,
    }


def test_flight_carbon_spec_falls_back_to_the_callsign() -> None:
    """The airline-level variant knows nothing about *this* flight's airports."""

    route = AirlineRoutesResult.model_validate(load_fixture("routes_airline"))

    spec = _flight_carbon_spec(route, callsign="BA123", aircraft_type=None)

    assert spec is not None and spec.query is not None
    assert spec.query["callsign"] == "BA123"
    assert spec.query["departure_icao"] is None


def test_flight_carbon_spec_avoids_the_equal_airport_422() -> None:
    route = VrsRouteResult.model_validate(
        {"source": "vrs", "departure_icao": "EGLL", "arrival_icao": "EGLL"}
    )

    spec = _flight_carbon_spec(route, callsign="BAW117", aircraft_type=None)

    assert spec is not None and spec.query is not None
    assert spec.query["callsign"] == "BAW117"


def test_flight_carbon_spec_is_none_without_anything_to_price() -> None:
    assert _flight_carbon_spec(None, callsign=None, aircraft_type=None) is None


def test_truncate_board_keeps_the_reported_total() -> None:
    board = DeparturesResponse.model_validate(
        {
            "total_flights": 85,
            "flights": [{"Flight": f"BA{index}"} for index in range(5)],
        }
    )

    cut = _truncate_board(board, 2)

    assert [flight.flight for flight in cut.flights] == ["BA0", "BA1"]
    assert cut.total_flights == 85
    assert _truncate_board(None, 2) is None


@pytest.mark.parametrize(
    ("continent", "expected"),
    [(None, True), ("", True), ("   ", True), ("NA", True), ("na", True), ("EU", False)],
)
def test_is_north_american_covers_every_spelling_of_empty(
    continent: str | None, expected: bool
) -> None:
    assert _is_north_american(Country.model_validate({"continent": continent})) is expected


def test_lookup_plan_dedupes_and_applies_the_budget() -> None:
    rows = [_adsb_row("4CA1FB"), _adsb_row("4ca1fb"), _adsb_row(None), _adsb_row("a1b2c3")]

    keys, wanted = _lookup_plan(rows, max_lookups=1)

    assert keys == ["4ca1fb", "4ca1fb", None, "a1b2c3"]
    assert wanted == ["4ca1fb"]


# ── airport_brief ────────────────────────────────────────────────────────────


def test_airport_brief_fills_every_part(compose: Compose, respx_mock: respx.MockRouter) -> None:
    routes = _mock_airport_brief(respx_mock)

    brief = compose.airport_brief(ICAO)

    assert isinstance(brief.airport, EnrichedAirport)
    assert isinstance(brief.metar, MetarWithParsed)
    assert isinstance(brief.taf, TafWithParsed)
    assert isinstance(brief.notams, NotamsResponse)
    assert isinstance(brief.delays, FaaDelayResponse)
    assert isinstance(brief.charts, ChartsResponse)
    assert isinstance(brief.departures, DeparturesResponse)
    assert isinstance(brief.arrivals, ArrivalsResponse)
    assert brief.errors == {}
    assert all(route.call_count == 1 for route in routes.values())
    # Weather is asked for decoded — a brief exists to be read.
    assert routes["metar"].calls.last.request.url.params["parsed"] == "true"


def test_airport_brief_one_dead_part_does_not_lose_the_others(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    """EGLL is not an FAA field: delays 404 and the brief is still worth having."""

    _mock_airport_brief(respx_mock)
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/delays/faa/{ICAO}").mock(
        return_value=httpx.Response(404, json={"detail": "airport not found"})
    )
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/charts/{ICAO}").mock(
        return_value=httpx.Response(503, json={"detail": "chart source down"})
    )

    brief = compose.airport_brief(ICAO)

    assert brief.delays is None
    assert brief.charts is None
    assert isinstance(brief.errors["delays"], NotFoundError)
    assert isinstance(brief.errors["charts"], ServiceUnavailableError)
    assert brief.metar is not None
    assert brief.airport is not None
    assert set(brief.errors) == {"delays", "charts"}


def test_airport_brief_raises_the_primary_failure(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    _mock_airport_brief(respx_mock)
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/airports/search").mock(
        return_value=httpx.Response(404, json={"detail": "Airport not found"})
    )

    with pytest.raises(NotFoundError):
        compose.airport_brief(ICAO)


def test_airport_brief_excluding_the_primary_skips_it_instead(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    routes = _mock_airport_brief(respx_mock)

    brief = compose.airport_brief(ICAO, exclude=("airport", "charts", "delays"))

    assert brief.airport is None
    assert brief.errors == {}
    assert routes["airport"].call_count == 0
    assert routes["charts"].call_count == 0
    assert routes["metar"].call_count == 1


def test_airport_brief_include_requests_nothing_else(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    routes = _mock_airport_brief(respx_mock)

    brief = compose.airport_brief(ICAO, include=("metar", "notams"))

    assert brief.metar is not None and brief.notams is not None
    assert brief.airport is None and brief.taf is None
    assert [name for name, route in routes.items() if route.call_count] == ["metar", "notams"]


def test_airport_brief_validates_before_any_request(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    routes = _mock_airport_brief(respx_mock)

    with pytest.raises(ValueError, match="not both"):
        compose.airport_brief(ICAO, include=("metar",), exclude=("taf",))
    with pytest.raises(ValueError, match="valid parts are"):
        compose.airport_brief(ICAO, include=("weather",))
    with pytest.raises(ValueError, match="schedules_limit"):
        compose.airport_brief(ICAO, schedules_limit=-1)

    assert all(route.call_count == 0 for route in routes.values())


def test_airport_brief_truncates_the_boards(compose: Compose, respx_mock: respx.MockRouter) -> None:
    board = dict(load_fixture("schedules_departures"))
    board["flights"] = [{"Flight": f"BA{index}"} for index in range(5)]
    _mock_airport_brief(respx_mock)
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/schedules/departures").mock(
        return_value=httpx.Response(200, json=board)
    )

    brief = compose.airport_brief(ICAO, schedules_limit=2)

    assert brief.departures is not None
    assert len(brief.departures.flights) == 2
    assert brief.departures.total_flights == 85  # untouched: "there is more"


def test_airport_brief_forwards_request_options(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    routes = _mock_airport_brief(respx_mock)

    compose.airport_brief(ICAO, request_options={"headers": {"X-Trace": "abc"}})

    for route in routes.values():
        assert route.calls.last.request.headers["x-trace"] == "abc"


def test_airport_brief_really_runs_in_parallel(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    """Eight parts, one slow handler each: they overlap and do not add up."""

    probe = _ConcurrencyProbe(delay=0.05)
    for path, payload in (
        ("/airports/search", load_fixture("airports_search")),
        (f"/weather/metar/{ICAO}", load_fixture("weather_metar_parsed")),
        (f"/weather/taf/{ICAO}", load_fixture("weather_taf_parsed")),
        (f"/notams/{ICAO}", load_fixture("notams")),
        (f"/delays/faa/{ICAO}", DELAYS_PAYLOAD),
        (f"/charts/{ICAO}", load_fixture("charts")),
        ("/schedules/departures", load_fixture("schedules_departures")),
        ("/schedules/arrivals", ARRIVALS_PAYLOAD),
    ):
        respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
            side_effect=probe.handler(payload)
        )

    started = time.monotonic()
    brief = compose.airport_brief(ICAO)
    elapsed = time.monotonic() - started

    assert brief.errors == {}
    assert probe.max_active >= 4, "the parts were not requested concurrently"
    assert elapsed < 8 * probe.delay / 2, "the parts were serialised"


def test_unexpected_exceptions_are_not_filed_as_a_part(client: SkyLink) -> None:
    """A defect in the SDK must surface, not masquerade as a dead endpoint."""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise TypeError("this is a defect, not an API failure")

    client.execute = boom  # type: ignore[method-assign]

    with pytest.raises(TypeError, match="defect"):
        Compose(client).airport_brief(ICAO)


# ── flight_brief ─────────────────────────────────────────────────────────────


def _mock_flight_brief(
    respx_mock: respx.MockRouter,
    *,
    status: dict[str, Any] | None = None,
    route_payload: Any = None,
) -> dict[str, respx.Route]:
    return {
        "status": respx_mock.get(url__startswith=f"{TEST_BASE_URL}/flight_status/").mock(
            return_value=httpx.Response(200, json=status or load_fixture("flight_status"))
        ),
        "aircraft": respx_mock.get(url__startswith=f"{TEST_BASE_URL}/aircraft/registration/").mock(
            return_value=httpx.Response(200, json=load_fixture("aircraft_found"))
        ),
        "route": respx_mock.get(url__startswith=f"{TEST_BASE_URL}/routes/callsign/").mock(
            return_value=httpx.Response(200, json=route_payload or load_fixture("routes_vrs"))
        ),
        "carbon": respx_mock.get(url__startswith=f"{TEST_BASE_URL}/carbon/estimate").mock(
            return_value=httpx.Response(200, json=load_fixture("carbon"))
        ),
    }


def test_flight_brief_without_a_registration_skips_the_airframe(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    """The live payload has no tail number: no lookup, no error — just no data."""

    routes = _mock_flight_brief(respx_mock)

    brief = compose.flight_brief("BA123")

    assert isinstance(brief.status, FlightStatusResponse)
    assert brief.aircraft is None
    assert "aircraft" not in brief.errors
    assert routes["aircraft"].call_count == 0
    # "BA 123" from the payload, unspaced, is what the route is looked up by.
    assert routes["route"].calls.last.request.url.path.endswith("/routes/callsign/BA123")
    assert isinstance(brief.route, VrsRouteResult)
    assert isinstance(brief.carbon, CarbonEstimate)


def test_flight_brief_skips_the_lookup_for_a_placeholder_registration(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    """A ``"--"`` registration is "no airframe", not an airframe — no request at all."""

    routes = _mock_flight_brief(respx_mock, status=_status_payload(registration="--"))

    brief = compose.flight_brief("BA117")

    assert brief.aircraft is None
    assert "aircraft" not in brief.errors
    assert routes["aircraft"].call_count == 0


def test_flight_brief_chains_registration_into_the_lookup(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    routes = _mock_flight_brief(respx_mock, status=_status_payload(registration="g-stba"))

    brief = compose.flight_brief("BA123")

    assert routes["aircraft"].calls.last.request.url.path.endswith("/aircraft/registration/G-STBA")
    assert isinstance(brief.aircraft, AircraftLookup)
    # The type found on the airframe sharpens the CO2 estimate.
    assert routes["carbon"].calls.last.request.url.params["aircraft_type"] == "B77W"
    assert routes["carbon"].calls.last.request.url.params["departure_icao"] == "EGLL"


def test_flight_brief_looks_the_route_up_by_what_the_caller_passed(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    """``/flight_status`` echoes the IATA form, so the payload must not win.

    ``BAW117`` comes back as ``"BA117"``; preferring that would downgrade every
    ICAO callsign to the airline-level route and lose the airport pair that
    prices the CO₂ (found on the live API, 2026-08-12).
    """

    routes = _mock_flight_brief(respx_mock, status=_status_payload(flight_number="BA 117"))

    brief = compose.flight_brief("BAW117")

    assert routes["route"].calls.last.request.url.path.endswith("/routes/callsign/BAW117")
    assert isinstance(brief.route, VrsRouteResult)


def test_flight_brief_falls_back_to_the_payload_number(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    """An unusable argument (the scraper's dashes) still leaves the payload."""

    routes = _mock_flight_brief(respx_mock)

    compose.flight_brief("--")

    assert routes["route"].calls.last.request.url.path.endswith("/routes/callsign/BA123")


def test_flight_brief_treats_a_not_found_airframe_as_data(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    """``found=false`` is a 200 sentinel, not a failure."""

    _mock_flight_brief(respx_mock, status=_status_payload(registration="ZZ-ZZZ"))
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/aircraft/registration/").mock(
        return_value=httpx.Response(200, json=load_fixture("aircraft_not_found"))
    )

    brief = compose.flight_brief("BA123")

    assert isinstance(brief.aircraft, AircraftLookup)
    assert brief.aircraft.found is False
    assert brief.errors == {}


def test_flight_brief_route_failure_falls_back_to_the_callsign_for_carbon(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    routes = _mock_flight_brief(respx_mock)
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/routes/callsign/").mock(
        return_value=httpx.Response(404, json={"detail": "callsign not in dataset"})
    )

    brief = compose.flight_brief("BA123")

    assert brief.route is None
    assert isinstance(brief.errors["route"], NotFoundError)
    assert routes["carbon"].calls.last.request.url.params["callsign"] == "BA123"
    assert isinstance(brief.carbon, CarbonEstimate)


def test_flight_brief_airline_fallback_route_prices_by_callsign(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    routes = _mock_flight_brief(respx_mock, route_payload=load_fixture("routes_airline"))

    brief = compose.flight_brief("BA123")

    assert isinstance(brief.route, AirlineRoutesResult)
    assert routes["carbon"].calls.last.request.url.params["callsign"] == "BA123"


def test_flight_brief_collects_a_carbon_failure(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    _mock_flight_brief(respx_mock)
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/carbon/estimate").mock(
        return_value=httpx.Response(500, json={"detail": "Carbon estimation failed"})
    )

    brief = compose.flight_brief("BA123")

    assert brief.carbon is None
    assert isinstance(brief.errors["carbon"], InternalServerError)
    assert brief.route is not None


def test_flight_brief_raises_the_primary_failure(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    routes = _mock_flight_brief(respx_mock)
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/flight_status/").mock(
        return_value=httpx.Response(404, json={"detail": "Flight not found"})
    )

    with pytest.raises(NotFoundError):
        compose.flight_brief("ZZ9999")

    assert routes["route"].call_count == 0


def test_flight_brief_include_stops_the_chain(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    routes = _mock_flight_brief(respx_mock)

    brief = compose.flight_brief("BA123", include=("status",))

    assert brief.status is not None
    assert brief.route is None and brief.carbon is None
    assert routes["route"].call_count == 0
    assert routes["carbon"].call_count == 0


# ── route_brief ──────────────────────────────────────────────────────────────


def test_route_brief_fills_every_part(compose: Compose, respx_mock: respx.MockRouter) -> None:
    routes = _mock_route_brief(respx_mock)

    brief = compose.route_brief("EGLL", "KJFK", aircraft_type="B77W", passengers=300)

    assert isinstance(brief.distance, DistanceResponse)
    assert isinstance(brief.flight_time, FlightTimePrediction)
    assert isinstance(brief.origin_metar, MetarWithParsed)
    assert isinstance(brief.destination_metar, MetarWithParsed)
    assert isinstance(brief.origin_taf, TafWithParsed)
    assert isinstance(brief.destination_taf, TafWithParsed)
    assert isinstance(brief.carbon, CarbonEstimate)
    assert brief.errors == {}
    assert routes["flight_time"].calls.last.request.url.params["aircraft"] == "B77W"
    assert routes["carbon"].calls.last.request.url.params["passengers"] == "300"


def test_route_brief_never_raises_for_a_part(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    """No primary here: a route brief with a hole is still a route brief."""

    _mock_route_brief(respx_mock)
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/distance").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/weather/taf/KJFK").mock(
        return_value=httpx.Response(404, json={"detail": "No TAF"})
    )

    brief = compose.route_brief("EGLL", "KJFK")

    assert brief.distance is None and brief.destination_taf is None
    assert set(brief.errors) == {"distance", "destination_taf"}
    assert brief.carbon is not None


def test_route_brief_exclude_saves_the_requests(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    routes = _mock_route_brief(respx_mock)

    brief = compose.route_brief("EGLL", "KJFK", exclude=("carbon", "origin_taf", "destination_taf"))

    assert brief.carbon is None and brief.origin_taf is None
    assert routes["carbon"].call_count == 0
    assert routes["origin_taf"].call_count == 0
    assert routes["distance"].call_count == 1


def test_route_brief_validates_before_any_request(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    routes = _mock_route_brief(respx_mock)

    with pytest.raises(ValueError, match="not both"):
        compose.route_brief("EGLL", "KJFK", include=("distance",), exclude=("carbon",))
    with pytest.raises(ValueError, match="valid parts are"):
        compose.route_brief("EGLL", "KJFK", exclude=("metar",))

    assert all(route.call_count == 0 for route in routes.values())


# ── enrich_adsb ──────────────────────────────────────────────────────────────


def _lookup_route(respx_mock: respx.MockRouter, **kwargs: Any) -> respx.Route:
    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}/aircraft/icao24/").mock(**kwargs)


def test_enrich_adsb_memoises_within_the_call(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    """The same airframe seen twice — including in two spellings — costs one request."""

    route = _lookup_route(
        respx_mock, return_value=httpx.Response(200, json=load_fixture("aircraft_found"))
    )
    rows = [_adsb_row("4ca1fb", "BAW117"), _adsb_row("4CA1FB", "BAW117"), _adsb_row(None)]

    enriched = compose.enrich_adsb(rows)

    assert route.call_count == 1
    assert route.calls.last.request.url.path.endswith("/aircraft/icao24/4ca1fb")
    assert route.calls.last.request.url.params["photos"] == "false"
    assert len(enriched) == 3
    assert enriched[0].info is enriched[1].info
    assert isinstance(enriched[0], EnrichedAircraft)
    assert enriched[2].info is None and enriched[2].error is None


def test_enrich_adsb_stops_at_max_lookups_without_requesting(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    route = _lookup_route(
        respx_mock, return_value=httpx.Response(200, json=load_fixture("aircraft_found"))
    )
    rows = [_adsb_row("aaa001"), _adsb_row("aaa002"), _adsb_row("aaa003")]

    enriched = compose.enrich_adsb(rows, max_lookups=1)

    assert route.call_count == 1
    assert enriched[0].info is not None
    assert enriched[1].info is None and enriched[1].error is None
    assert enriched[2].info is None and enriched[2].error is None


def test_enrich_adsb_files_the_failure_on_its_own_row(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{TEST_BASE_URL}/aircraft/icao24/aaa001").mock(
        return_value=httpx.Response(200, json=load_fixture("aircraft_found"))
    )
    respx_mock.get(f"{TEST_BASE_URL}/aircraft/icao24/aaa002").mock(
        return_value=httpx.Response(503, json={"detail": "registry loading"})
    )

    enriched = compose.enrich_adsb([_adsb_row("aaa001"), _adsb_row("aaa002")])

    assert enriched[0].info is not None and enriched[0].error is None
    assert enriched[1].info is None
    assert isinstance(enriched[1].error, ServiceUnavailableError)


def test_enrich_adsb_zero_budget_and_empty_input_make_no_requests(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    route = _lookup_route(respx_mock, return_value=httpx.Response(200, json={}))

    assert compose.enrich_adsb([]) == []
    assert [row.info for row in compose.enrich_adsb([_adsb_row("aaa001")], max_lookups=0)] == [None]
    assert route.call_count == 0

    with pytest.raises(ValueError, match="max_lookups"):
        compose.enrich_adsb([_adsb_row("aaa001")], max_lookups=-1)
    with pytest.raises(ValueError, match="concurrency"):
        compose.enrich_adsb([_adsb_row("aaa001")], concurrency=0)


def test_enrich_adsb_really_runs_in_parallel(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    probe = _ConcurrencyProbe(delay=0.05)
    _lookup_route(respx_mock, side_effect=probe.handler(load_fixture("aircraft_found")))
    rows = [_adsb_row(f"aaa00{index}") for index in range(5)]

    started = time.monotonic()
    compose.enrich_adsb(rows, concurrency=5)
    elapsed = time.monotonic() - started

    assert probe.max_active >= 3
    assert elapsed < 5 * probe.delay / 2


# ── schedules_with_status ────────────────────────────────────────────────────

BOARD_PAYLOAD: dict[str, Any] = {
    "iata": "LHR",
    "direction": "departures",
    "airport_code": "EGLL",
    "flights": [
        {"Time": "16:05", "IATA": "JFK", "Destination": "New York", "Flight": "BA 117"},
        {"Time": "16:10", "IATA": "CDG", "Destination": "Paris", "Flight": "BA117"},
        {"Time": "16:20", "IATA": "MAD", "Destination": "Madrid", "Flight": "IB3160"},
        {"Time": "16:30", "IATA": "AMS", "Destination": "Amsterdam", "Flight": None},
        {"Time": "16:40", "IATA": "DUB", "Destination": "Dublin", "Flight": "EI155"},
    ],
    "total_flights": 85,
}


def test_schedules_with_status_joins_the_board(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    board = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/schedules/departures").mock(
        return_value=httpx.Response(200, json=BOARD_PAYLOAD)
    )
    statuses = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/flight_status/").mock(
        return_value=httpx.Response(200, json=load_fixture("flight_status"))
    )

    rows = compose.schedules_with_status("EGLL", limit=4)

    assert board.calls.last.request.url.params["icao"] == "EGLL"
    assert len(rows) == 4
    assert isinstance(rows[0], ScheduleWithStatus)
    # PascalCase on the wire, snake_case on the model; "BA 117" and "BA117" are
    # the same flight, so three distinct numbers minus the empty row = 2 requests.
    assert rows[0].entry.flight == "BA 117"
    assert statuses.call_count == 2
    assert rows[0].status is rows[1].status
    assert rows[3].status is None and rows[3].error is None  # the row with no number


def test_schedules_with_status_files_per_row_failures(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/schedules/departures").mock(
        return_value=httpx.Response(200, json=BOARD_PAYLOAD)
    )
    respx_mock.get(f"{TEST_BASE_URL}/flight_status/BA117").mock(
        return_value=httpx.Response(200, json=load_fixture("flight_status"))
    )
    respx_mock.get(f"{TEST_BASE_URL}/flight_status/IB3160").mock(
        return_value=httpx.Response(404, json={"detail": "Flight not found"})
    )

    rows = compose.schedules_with_status("EGLL", limit=3)

    assert rows[0].status is not None
    assert rows[2].status is None
    assert isinstance(rows[2].error, NotFoundError)


def test_schedules_with_status_takes_arrivals_and_iata_codes(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    board = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/schedules/arrivals").mock(
        return_value=httpx.Response(200, json=ARRIVALS_PAYLOAD)
    )
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/flight_status/").mock(
        return_value=httpx.Response(200, json=load_fixture("flight_status"))
    )

    rows = compose.schedules_with_status("LHR", direction="arrivals")

    assert board.calls.last.request.url.params["iata"] == "LHR"
    assert rows[0].entry.origin == "Madrid"  # type: ignore[union-attr]


def test_schedules_with_status_validates_and_propagates_the_board(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/schedules/").mock(
        return_value=httpx.Response(503, json={"detail": "schedule source down"})
    )

    with pytest.raises(ValueError, match="direction"):
        compose.schedules_with_status("EGLL", direction="both")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="limit"):
        compose.schedules_with_status("EGLL", limit=-2)
    with pytest.raises(ValueError, match="concurrency"):
        # Checked before the board request, not after it.
        compose.schedules_with_status("EGLL", concurrency=0)
    assert route.call_count == 0

    with pytest.raises(ServiceUnavailableError):
        compose.schedules_with_status("EGLL")


def test_schedules_with_status_zero_limit_asks_for_no_status(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/schedules/departures").mock(
        return_value=httpx.Response(200, json=BOARD_PAYLOAD)
    )
    statuses = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/flight_status/").mock(
        return_value=httpx.Response(200, json=load_fixture("flight_status"))
    )

    assert compose.schedules_with_status("EGLL", limit=0) == []
    assert statuses.call_count == 0


# ── north_america_countries ──────────────────────────────────────────────────


def test_north_america_countries_recovers_the_lost_continent(
    compose: Compose, respx_mock: respx.MockRouter
) -> None:
    """``continent="NA"`` is NaN to pandas; the countries arrive with an empty one."""

    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/countries").mock(
        return_value=httpx.Response(200, json=COUNTRIES_PAYLOAD)
    )

    countries = compose.north_america_countries()

    assert [country.code for country in countries] == ["US", "CA", "MX"]
    assert all(isinstance(country, Country) for country in countries)
    # No continent filter is sent — that is exactly the parameter that fails.
    assert "continent" not in route.calls.last.request.url.params


# ── async mirror ─────────────────────────────────────────────────────────────


async def test_async_airport_brief_collects_and_raises_like_the_sync_one(
    async_compose: AsyncCompose, respx_mock: respx.MockRouter
) -> None:
    _mock_airport_brief(respx_mock)
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/delays/faa/{ICAO}").mock(
        return_value=httpx.Response(404, json={"detail": "airport not found"})
    )

    brief = await async_compose.airport_brief(ICAO)

    assert isinstance(brief.metar, MetarWithParsed)
    assert isinstance(brief.errors["delays"], NotFoundError)

    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/airports/search").mock(
        return_value=httpx.Response(403, json={"detail": "plan required"})
    )
    with pytest.raises(SkyLinkError):
        await async_compose.airport_brief(ICAO)


async def test_async_airport_brief_really_runs_in_parallel(
    async_compose: AsyncCompose, respx_mock: respx.MockRouter
) -> None:
    probe = _ConcurrencyProbe(delay=0.05)
    for path, payload in (
        ("/airports/search", load_fixture("airports_search")),
        (f"/weather/metar/{ICAO}", load_fixture("weather_metar_parsed")),
        (f"/weather/taf/{ICAO}", load_fixture("weather_taf_parsed")),
        (f"/notams/{ICAO}", load_fixture("notams")),
        (f"/delays/faa/{ICAO}", DELAYS_PAYLOAD),
        (f"/charts/{ICAO}", load_fixture("charts")),
        ("/schedules/departures", load_fixture("schedules_departures")),
        ("/schedules/arrivals", ARRIVALS_PAYLOAD),
    ):
        respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
            side_effect=probe.async_handler(payload)
        )

    started = time.monotonic()
    brief = await async_compose.airport_brief(ICAO)
    elapsed = time.monotonic() - started

    assert brief.errors == {}
    assert probe.max_active >= 4
    assert elapsed < 8 * probe.delay / 2


async def test_async_flight_brief_chain(
    async_compose: AsyncCompose, respx_mock: respx.MockRouter
) -> None:
    routes = _mock_flight_brief(respx_mock, status=_status_payload(registration="G-STBA"))

    brief = await async_compose.flight_brief("BA123")

    assert isinstance(brief.status, FlightStatusResponse)
    assert isinstance(brief.aircraft, AircraftLookup)
    assert isinstance(brief.route, VrsRouteResult)
    assert isinstance(brief.carbon, CarbonEstimate)
    assert routes["carbon"].calls.last.request.url.params["arrival_icao"] == "KJFK"


async def test_async_route_brief_collects_failures(
    async_compose: AsyncCompose, respx_mock: respx.MockRouter
) -> None:
    _mock_route_brief(respx_mock)
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/ml/flight-time").mock(
        return_value=httpx.Response(500, json={"detail": "model down"})
    )

    brief = await async_compose.route_brief("EGLL", "KJFK")

    assert brief.flight_time is None
    assert isinstance(brief.errors["flight_time"], InternalServerError)
    assert brief.distance is not None


async def test_async_enrich_adsb_memoises_and_caps(
    async_compose: AsyncCompose, respx_mock: respx.MockRouter
) -> None:
    route = _lookup_route(
        respx_mock, return_value=httpx.Response(200, json=load_fixture("aircraft_found"))
    )
    rows = [_adsb_row("aaa001"), _adsb_row("aaa001"), _adsb_row("aaa002")]

    enriched = await async_compose.enrich_adsb(rows, max_lookups=1)

    assert route.call_count == 1
    assert enriched[0].info is enriched[1].info
    assert enriched[2].info is None and enriched[2].error is None


async def test_async_schedules_with_status_and_countries(
    async_compose: AsyncCompose, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/schedules/departures").mock(
        return_value=httpx.Response(200, json=BOARD_PAYLOAD)
    )
    respx_mock.get(f"{TEST_BASE_URL}/flight_status/BA117").mock(
        return_value=httpx.Response(200, json=load_fixture("flight_status"))
    )
    respx_mock.get(f"{TEST_BASE_URL}/flight_status/IB3160").mock(
        return_value=httpx.Response(404, json={"detail": "Flight not found"})
    )
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/countries").mock(
        return_value=httpx.Response(200, json=COUNTRIES_PAYLOAD)
    )

    rows = await async_compose.schedules_with_status("EGLL", limit=3)
    countries = await async_compose.north_america_countries()

    assert [row.entry.flight for row in rows] == ["BA 117", "BA117", "IB3160"]
    assert isinstance(rows[2].error, NotFoundError)
    assert [country.code for country in countries] == ["US", "CA", "MX"]
