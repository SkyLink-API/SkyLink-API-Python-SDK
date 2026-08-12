"""Wiring and public surface (task A8).

Three things are checked here and nowhere else:

* every namespace is attached to both clients, and the async client hands back
  the ``Async*`` class rather than its blocking twin;
* the two one-method shortcuts ``sky.flight_status(...)`` / ``sky.distance(...)``
  reach the endpoint (the ``FlightStatus``/``Distance`` instances themselves stay
  private);
* every name in ``skylink_api.__all__`` is importable from the package root.
"""

from __future__ import annotations

import importlib
from typing import Any

import httpx
import pytest
import respx

import skylink_api
from conftest import TEST_BASE_URL, TEST_PROVIDER
from skylink_api import AsyncSkyLink, SkyLink
from skylink_api.models.distance import DistanceResponse
from skylink_api.models.flight_status import FlightStatusResponse
from skylink_api.resources import (
    Adsb,
    Aircraft,
    Airlines,
    Airports,
    AsyncAdsb,
    AsyncAircraft,
    AsyncAirlines,
    AsyncAirports,
    AsyncBatch,
    AsyncBriefing,
    AsyncCarbon,
    AsyncCharts,
    AsyncCompose,
    AsyncDelays,
    AsyncDistance,
    AsyncFlightStatus,
    AsyncGeo,
    AsyncHistory,
    AsyncMl,
    AsyncNavaids,
    AsyncNotams,
    AsyncPoll,
    AsyncRoutes,
    AsyncSchedules,
    AsyncTickets,
    AsyncWeather,
    AsyncWebhooks,
    Batch,
    Briefing,
    Carbon,
    Charts,
    Compose,
    Delays,
    Distance,
    FlightStatus,
    Geo,
    History,
    Ml,
    Navaids,
    Notams,
    Poll,
    Routes,
    Schedules,
    Tickets,
    Weather,
    Webhooks,
)

#: ``attribute -> (sync class, async class)`` for all 21 namespaces the client
#: exposes — the eighteen endpoint namespaces plus the three DX ones
#: (``batch``, ``poll``, ``compose``). ``flight_status`` and ``distance`` are
#: deliberately absent: they are methods, not namespaces.
#:
#: Adding a namespace without adding it here is exactly the mistake this file
#: exists to catch, so the count is asserted below.
NAMESPACES: dict[str, tuple[type, type]] = {
    "weather": (Weather, AsyncWeather),
    "airports": (Airports, AsyncAirports),
    "airlines": (Airlines, AsyncAirlines),
    "navaids": (Navaids, AsyncNavaids),
    "geo": (Geo, AsyncGeo),
    "adsb": (Adsb, AsyncAdsb),
    "aircraft": (Aircraft, AsyncAircraft),
    "charts": (Charts, AsyncCharts),
    "delays": (Delays, AsyncDelays),
    "notams": (Notams, AsyncNotams),
    "schedules": (Schedules, AsyncSchedules),
    "ml": (Ml, AsyncMl),
    "carbon": (Carbon, AsyncCarbon),
    "briefing": (Briefing, AsyncBriefing),
    "routes": (Routes, AsyncRoutes),
    "tickets": (Tickets, AsyncTickets),
    "webhooks": (Webhooks, AsyncWebhooks),
    "history": (History, AsyncHistory),
    "batch": (Batch, AsyncBatch),
    "poll": (Poll, AsyncPoll),
    "compose": (Compose, AsyncCompose),
}

FLIGHT_STATUS_PAYLOAD: dict[str, Any] = {
    "flight_number": "BA123",
    "airline": "British Airways",
    "status": "Landed",
    "departure": {"airport": "London Heathrow", "iata": "LHR", "scheduled_time": "10:30"},
    "arrival": {"airport": "New York JFK", "iata": "JFK", "estimated_time": "13:45"},
}

DISTANCE_PAYLOAD: dict[str, Any] = {
    "from_point": {"latitude": 40.639751, "longitude": -73.778925, "icao_code": "KJFK"},
    "to_point": {"latitude": 51.4706, "longitude": -0.461941, "icao_code": "EGLL"},
    "distance": 2991.01,
    "unit": "nm",
    "bearing": 51.35,
    "bearing_cardinal": "NE",
}


@pytest.fixture
def sync_client() -> SkyLink:
    return SkyLink(api_key="test", provider=TEST_PROVIDER, environ={})


@pytest.fixture
def async_only_client() -> AsyncSkyLink:
    return AsyncSkyLink(api_key="test", provider=TEST_PROVIDER, environ={})


# ── namespace wiring ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(NAMESPACES))
def test_sync_client_exposes_every_namespace(sync_client: SkyLink, name: str) -> None:
    expected, _ = NAMESPACES[name]
    assert isinstance(getattr(sync_client, name), expected)


@pytest.mark.parametrize("name", sorted(NAMESPACES))
def test_async_client_exposes_every_namespace(async_only_client: AsyncSkyLink, name: str) -> None:
    _, expected = NAMESPACES[name]
    namespace = getattr(async_only_client, name)
    assert isinstance(namespace, expected)


@pytest.mark.parametrize("name", sorted(NAMESPACES))
def test_async_namespaces_are_never_the_sync_class(
    async_only_client: AsyncSkyLink, name: str
) -> None:
    """A copy-paste slip in the wiring would hand back the blocking class."""

    sync_cls, async_cls = NAMESPACES[name]
    assert type(getattr(async_only_client, name)) is async_cls
    assert not isinstance(getattr(async_only_client, name), sync_cls)


def test_every_namespace_of_the_resources_package_is_covered() -> None:
    """Derived from ``resources.__all__``, so a new namespace cannot be forgotten."""

    from skylink_api import resources

    exported = {
        name
        for name in resources.__all__
        if not name.startswith("Async") and isinstance(getattr(resources, name), type)
    }
    # ``FlightStatus``/``Distance`` are methods on the client, not namespaces.
    assert exported - {"FlightStatus", "Distance"} == {
        sync_cls.__name__ for sync_cls, _ in NAMESPACES.values()
    } - {"FlightStatus", "Distance"}
    assert len(NAMESPACES) == 21


@pytest.mark.parametrize("name", sorted(NAMESPACES))
def test_namespaces_are_cached(sync_client: SkyLink, name: str) -> None:
    """``cached_property`` — repeated access must not rebuild the namespace."""

    assert getattr(sync_client, name) is getattr(sync_client, name)


def test_namespaces_hold_the_owning_client(sync_client: SkyLink) -> None:
    assert sync_client.weather._client is sync_client
    assert sync_client.history._client is sync_client


# ── shortcut methods ─────────────────────────────────────────────────────────


def test_shortcut_namespaces_stay_private(sync_client: SkyLink) -> None:
    """``flight_status``/``distance`` are methods; the instances are internal."""

    assert callable(sync_client.flight_status)
    assert callable(sync_client.distance)
    assert isinstance(sync_client._flight_status, FlightStatus)
    assert isinstance(sync_client._distance, Distance)
    assert isinstance(AsyncSkyLink(api_key="test", environ={})._flight_status, AsyncFlightStatus)
    assert isinstance(AsyncSkyLink(api_key="test", environ={})._distance, AsyncDistance)


@respx.mock
def test_flight_status_shortcut_calls_the_endpoint(sync_client: SkyLink) -> None:
    route = respx.get(f"{TEST_BASE_URL}/flight_status/BA123").mock(
        return_value=httpx.Response(200, json=FLIGHT_STATUS_PAYLOAD)
    )

    status = sync_client.flight_status("BA123")

    assert route.called
    assert isinstance(status, FlightStatusResponse)
    assert status.flight_number == "BA123"


@respx.mock
def test_distance_shortcut_passes_every_argument(sync_client: SkyLink) -> None:
    route = respx.get(url__startswith=f"{TEST_BASE_URL}/distance").mock(
        return_value=httpx.Response(200, json=DISTANCE_PAYLOAD)
    )

    leg = sync_client.distance(from_icao="KJFK", to_icao="EGLL", unit="nm")

    assert isinstance(leg, DistanceResponse)
    assert leg.distance == 2991.01
    params = route.calls.last.request.url.params
    assert params["from_icao"] == "KJFK"
    assert params["to_icao"] == "EGLL"
    assert params["unit"] == "nm"


@respx.mock
def test_distance_shortcut_accepts_coordinates(sync_client: SkyLink) -> None:
    route = respx.get(url__startswith=f"{TEST_BASE_URL}/distance").mock(
        return_value=httpx.Response(200, json=DISTANCE_PAYLOAD)
    )

    sync_client.distance(from_lat=40.64, from_lon=-73.78, to_lat=51.47, to_lon=-0.46, unit="km")

    params = route.calls.last.request.url.params
    assert params["from_lat"] == "40.64"
    assert params["to_lon"] == "-0.46"
    assert params["unit"] == "km"


@respx.mock
async def test_async_flight_status_shortcut(async_only_client: AsyncSkyLink) -> None:
    respx.get(f"{TEST_BASE_URL}/flight_status/BA123").mock(
        return_value=httpx.Response(200, json=FLIGHT_STATUS_PAYLOAD)
    )

    async with async_only_client as sky:
        status = await sky.flight_status("BA123")

    assert isinstance(status, FlightStatusResponse)
    assert status.airline == "British Airways"


@respx.mock
async def test_async_distance_shortcut(async_only_client: AsyncSkyLink) -> None:
    respx.get(url__startswith=f"{TEST_BASE_URL}/distance").mock(
        return_value=httpx.Response(200, json=DISTANCE_PAYLOAD)
    )

    async with async_only_client as sky:
        leg = await sky.distance(from_icao="KJFK", to_icao="EGLL")

    assert isinstance(leg, DistanceResponse)
    assert leg.bearing_cardinal == "NE"


# ── package exports ──────────────────────────────────────────────────────────


def test_root_all_is_sorted_and_unique() -> None:
    names = skylink_api.__all__
    assert len(names) == len(set(names))
    assert list(names) == sorted(names)


@pytest.mark.parametrize("name", skylink_api.__all__)
def test_every_exported_name_is_importable(name: str) -> None:
    """``from skylink_api import <name>`` for each entry of ``__all__``."""

    module = importlib.import_module("skylink_api")
    assert getattr(module, name) is not None


def test_root_exports_the_clients_and_version() -> None:
    assert skylink_api.SkyLink is SkyLink
    assert skylink_api.AsyncSkyLink is AsyncSkyLink
    assert isinstance(skylink_api.__version__, str)
    assert skylink_api.__version__


@pytest.mark.parametrize(
    "name",
    [
        "APIConnectionError",
        "APIResponseValidationError",
        "APIStatusError",
        "APITimeoutError",
        "AuthenticationError",
        "BadRequestError",
        "InternalServerError",
        "NotFoundError",
        "PermissionDeniedError",
        "RateLimitError",
        "ServiceUnavailableError",
        "SkyLinkError",
        "UnprocessableEntityError",
    ],
)
def test_error_hierarchy_is_exported_and_rooted_at_skylink_error(name: str) -> None:
    error_cls = getattr(skylink_api, name)
    assert issubclass(error_cls, skylink_api.SkyLinkError)
    assert name in skylink_api.__all__


def test_config_types_are_exported() -> None:
    for name in ("Provider", "HistoryPlan", "BBox", "RequestOptions", "RateLimitInfo"):
        assert name in skylink_api.__all__
        assert getattr(skylink_api, name) is not None


def test_models_are_re_exported_flat_without_collisions() -> None:
    """Every ``models/*.py`` name reaches both ``skylink_api.models`` and the root."""

    from skylink_api import models

    assert len(models.__all__) == len(set(models.__all__))
    for name in models.__all__:
        assert getattr(models, name) is getattr(skylink_api, name), name


def test_resource_and_model_names_do_not_shadow_each_other() -> None:
    """``FlightStatus``/``FlightStatusResponse`` and ``Distance``/``DistanceResponse``.

    The resource classes are not exported from the root, so the model names own
    the top-level namespace unambiguously.
    """

    from skylink_api import models

    assert "FlightStatusResponse" in models.__all__
    assert "DistanceResponse" in models.__all__
    assert "FlightStatus" not in skylink_api.__all__
    assert "Distance" not in skylink_api.__all__
    assert skylink_api.FlightStatusResponse is not FlightStatus
    assert skylink_api.DistanceResponse is not Distance
