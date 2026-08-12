"""``sky.charts`` — aerodrome chart links.

``charts.json`` is verbatim from the router's OpenAPI example and is exactly the
shape that matters: a **partial** category map (only ``GND`` and ``SID`` — the
other three categories are dropped server-side rather than sent empty).

The namespace is not attached to the client yet (task A8 does the wiring), so
the tests instantiate ``Charts``/``AsyncCharts`` directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import NotFoundError
from skylink_api.models.charts import ChartSourcesResponse, ChartsResponse
from skylink_api.resources.charts import (
    AsyncCharts,
    Charts,
    _by_airport_spec,
    _by_category_spec,
    _sources_spec,
)

SOURCES_PAYLOAD: dict[str, Any] = {
    "sources": [
        {"source_id": "faa", "name": "FAA (United States)", "icao_prefixes": ["K", "P"]},
        {"source_id": "uk", "name": "UK NATS AIP", "icao_prefixes": ["EG"]},
        {
            "source_id": "russia",
            "name": "Russia CAI",
            "icao_prefixes": ["U* (except UA,UC,UG,UM,UT,UZ)"],
        },
    ],
    "total_count": 3,
}


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}{path}`` (query independent) with a JSON body."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


@pytest.fixture
def charts(client: SkyLink) -> Charts:
    return Charts(client)


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builders_produce_the_documented_specs() -> None:
    airport = _by_airport_spec("KJFK")
    assert (airport.method, airport.path) == ("GET", "/charts/KJFK")
    assert airport.query == {"source": None}
    assert airport.cast_to is ChartsResponse

    assert _by_airport_spec("KJFK", source="faa").query == {"source": "faa"}

    category = _by_category_spec("KJFK", "APP")
    assert category.path == "/charts/KJFK/APP"
    assert category.query == {"source": None}
    assert category.cast_to is ChartsResponse

    sources = _sources_spec()
    assert sources.path == "/charts/sources"
    assert sources.query is None
    assert sources.cast_to is ChartSourcesResponse


# ── by airport ───────────────────────────────────────────────────────────────


def test_by_airport_partial_category_map(charts: Charts, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/charts/KJFK", load_fixture("charts"))

    result = charts.by_airport("KJFK")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/charts/KJFK"
    assert "source" not in request.url.params
    assert request.headers["x-api-key"] == "test-key"

    assert isinstance(result, ChartsResponse)
    assert result.icao_code == "KJFK"
    assert result.source == "faa"
    assert result.total_count == 42
    assert result.fetched_at == datetime(2026, 2, 11, 12, 0, tzinfo=timezone.utc)

    # PARTIAL map: empty categories are dropped server-side, not sent as [].
    assert set(result.charts) == {"GND", "SID"}
    assert "APP" not in result.charts
    assert "GEN" not in result.charts
    assert "STAR" not in result.charts
    # ...so `.get(category, [])` is the safe access pattern.
    assert result.charts.get("APP", []) == []

    diagram = result.charts["GND"][0]
    assert diagram.name == "KJFK - Airport Diagram"
    assert diagram.category == "GND"
    assert diagram.url == "https://..."


def test_by_airport_with_source_override(charts: Charts, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/charts/EGLL", {**load_fixture("charts"), "icao_code": "EGLL"})

    charts.by_airport("EGLL", source="uk")

    assert route.calls.last.request.url.params["source"] == "uk"


def test_by_airport_empty_map_and_unknown_fields(
    charts: Charts, respx_mock: respx.MockRouter
) -> None:
    _mock(
        respx_mock,
        "/charts/LFPG",
        {"icao_code": "LFPG", "source": "france", "revision": "AIRAC 2602"},
    )

    result = charts.by_airport("LFPG")

    # `charts` missing entirely still parses — defaults to an empty map.
    assert result.charts == {}
    assert result.total_count == 0
    assert result.fetched_at is None
    # extra="allow": backend additions never break a pinned SDK.
    assert result.model_extra is not None
    assert result.model_extra["revision"] == "AIRAC 2602"


def test_by_airport_404_raises(charts: Charts, respx_mock: respx.MockRouter) -> None:
    """An airport with no charts is a 404, not an empty 200."""

    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/charts/ZZZZ").mock(
        return_value=httpx.Response(
            404, json={"detail": "Charts for ZZZZ are not currently available."}
        )
    )

    with pytest.raises(NotFoundError) as excinfo:
        charts.by_airport("ZZZZ")

    assert excinfo.value.status_code == 404


def test_by_airport_request_options_are_forwarded(
    charts: Charts, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/charts/KJFK", load_fixture("charts"))

    charts.by_airport(
        "KJFK", request_options={"headers": {"X-Trace": "abc"}, "query": {"debug": True}}
    )

    request = route.calls.last.request
    assert request.headers["X-Trace"] == "abc"
    assert request.url.params["debug"] == "true"


# ── by category ──────────────────────────────────────────────────────────────


def test_by_category_narrows_to_one_key(charts: Charts, respx_mock: respx.MockRouter) -> None:
    payload = {
        **load_fixture("charts"),
        "charts": {"SID": load_fixture("charts")["charts"]["SID"]},
        "total_count": 1,
    }
    route = _mock(respx_mock, "/charts/KJFK/SID", payload)

    result = charts.by_category("KJFK", "SID")

    assert route.calls.last.request.url.path == "/v3.1/charts/KJFK/SID"
    assert set(result.charts) == {"SID"}
    # total_count is the FILTERED count, not the airport's total.
    assert result.total_count == 1
    assert result.charts["SID"][0].name == "KENNEDY TWO"


def test_by_category_with_source_override(charts: Charts, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/charts/RJTT/APP", {"icao_code": "RJTT", "source": "japan"})

    charts.by_category("RJTT", "APP", source="japan")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/charts/RJTT/APP"
    assert request.url.params["source"] == "japan"


def test_by_category_404_raises(charts: Charts, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/charts/KJFK/GEN").mock(
        return_value=httpx.Response(404, json={"detail": "No GEN charts found for KJFK"})
    )

    with pytest.raises(NotFoundError) as excinfo:
        charts.by_category("KJFK", "GEN")

    assert excinfo.value.status_code == 404


# ── sources ──────────────────────────────────────────────────────────────────


def test_sources(charts: Charts, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/charts/sources", SOURCES_PAYLOAD)

    result = charts.sources()

    assert route.calls.last.request.url.path == "/v3.1/charts/sources"
    assert isinstance(result, ChartSourcesResponse)
    assert result.total_count == 3
    assert [source.source_id for source in result.sources] == ["faa", "uk", "russia"]
    assert result.sources[0].icao_prefixes == ["K", "P"]
    # The catch-all Russian source carries a descriptive SENTINEL, not a prefix.
    assert result.sources[2].icao_prefixes == ["U* (except UA,UC,UG,UM,UT,UZ)"]


def test_sources_missing_prefixes_default_to_empty(
    charts: Charts, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, "/charts/sources", {"sources": [{"source_id": "faa"}]})

    result = charts.sources()

    assert result.sources[0].icao_prefixes == []
    assert result.total_count == 0


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_by_airport(async_client: AsyncSkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/charts/KJFK", load_fixture("charts"))

    result = await AsyncCharts(async_client).by_airport("KJFK", source="faa")

    assert route.calls.last.request.url.params["source"] == "faa"
    assert isinstance(result, ChartsResponse)
    assert set(result.charts) == {"GND", "SID"}


async def test_async_by_category_and_sources(
    async_client: AsyncSkyLink, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, "/charts/sources", SOURCES_PAYLOAD)
    _mock(respx_mock, "/charts/EGLL/STAR", {"icao_code": "EGLL", "source": "uk", "charts": {}})

    charts = AsyncCharts(async_client)

    sources = await charts.sources()
    star = await charts.by_category("EGLL", "STAR")

    assert sources.total_count == 3
    assert star.charts == {}
