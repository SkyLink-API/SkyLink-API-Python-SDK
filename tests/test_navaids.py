"""``sky.navaids`` — filtered navaid search.

Payloads are built inline from ``models/v3/navaids.py`` (the route has no
OpenAPI example block). Covers the two things unique to this namespace: the
client-side "at least one filter" guard and the ``usageType`` alias.

The namespace is not attached to the client yet (task A8 does the wiring), so
the resource classes are instantiated directly here.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import BadRequestError
from skylink_api.models.navaids import Navaid, NavaidsResponse
from skylink_api.resources.navaids import AsyncNavaids, Navaids, _list_spec

BBOX = (40.0, -80.0, 42.0, -73.0)
BBOX_WIRE = "40.0,-80.0,42.0,-73.0"

JFK_VOR: dict[str, Any] = {
    "id": 89745,
    "ident": "JFK",
    "name": "Kennedy",
    "type": "VOR-DME",
    "frequency_khz": 115900.0,
    "latitude_deg": 40.6329994202,
    "longitude_deg": -73.7761993408,
    "elevation_ft": 12.0,
    "iso_country": "US",
    "dme_frequency_khz": 115900.0,
    "dme_channel": "106X",
    "slaved_variation_deg": -13.0,
    "magnetic_variation_deg": -13.226,
    "usageType": "BOTH",
    "power": "HIGH",
    "associated_airport": "KJFK",
}

PAYLOAD: dict[str, Any] = {
    "navaids": [JFK_VOR],
    "total": 1,
    "filters_applied": {"airport": "KJFK"},
}


@pytest.fixture
def navaids(client: SkyLink) -> Navaids:
    return Navaids(client)


@pytest.fixture
def async_navaids(async_client: AsyncSkyLink) -> AsyncNavaids:
    return AsyncNavaids(async_client)


def _mock(respx_mock: respx.MockRouter, payload: Any) -> respx.Route:
    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}/navaids").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builder_produces_the_documented_spec() -> None:
    spec = _list_spec(airport="KJFK")
    assert spec.method == "GET"
    assert spec.path == "/navaids"
    assert spec.cast_to is NavaidsResponse
    assert spec.query == {
        "ident": None,
        "airport": "KJFK",
        "type": None,
        "country": None,
        "bbox": None,
        "limit": 100,
    }

    # bbox tuples are flattened by the builder, not by the transport.
    assert _list_spec(bbox=BBOX).query == {
        "ident": None,
        "airport": None,
        "type": None,
        "country": None,
        "bbox": BBOX_WIRE,
        "limit": 100,
    }
    assert _list_spec(bbox="40,-80,42,-73").query["bbox"] == "40,-80,42,-73"


def test_builder_requires_at_least_one_filter() -> None:
    with pytest.raises(ValueError, match="at least one filter"):
        _list_spec()

    # limit alone is not a filter.
    with pytest.raises(ValueError, match="ident, airport, type, country, bbox"):
        _list_spec(limit=10)

    # ...but any single one is enough.
    for kwargs in (
        {"ident": "JFK"},
        {"airport": "KJFK"},
        {"type": "VOR"},
        {"country": "US"},
        {"bbox": BBOX},
    ):
        assert _list_spec(**kwargs).path == "/navaids"  # type: ignore[arg-type]


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_without_filters_never_sends_a_request(
    navaids: Navaids, respx_mock: respx.MockRouter
) -> None:
    """~70K rows unfiltered — the API 400s, so the SDK stops it client side."""

    route = _mock(respx_mock, PAYLOAD)

    with pytest.raises(ValueError, match="at least one filter"):
        navaids.list()
    with pytest.raises(ValueError, match="at least one filter"):
        navaids.list(limit=500)

    assert route.call_count == 0


def test_list_by_airport(navaids: Navaids, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, PAYLOAD)

    result = navaids.list(airport="KJFK")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/navaids"
    assert request.url.params["airport"] == "KJFK"
    assert request.url.params["limit"] == "100"
    for dropped in ("ident", "type", "country", "bbox"):
        assert dropped not in request.url.params
    assert request.headers["x-api-key"] == "test-key"

    assert isinstance(result, NavaidsResponse)
    assert result.total == 1
    navaid = result.navaids[0]
    assert isinstance(navaid, Navaid)
    assert navaid.ident == "JFK"
    assert navaid.type == "VOR-DME"
    assert navaid.associated_airport == "KJFK"
    assert navaid.power == "HIGH"
    # usageType is the only camelCase key in the API.
    assert navaid.usage_type == "BOTH"
    # filters_applied is a genuine dynamic map, echoed back normalised.
    assert result.filters_applied == {"airport": "KJFK"}


def test_usage_type_alias_round_trips() -> None:
    """Constructible by attribute name; serialised back under the wire key."""

    by_wire = Navaid.model_validate({"ident": "JFK", "usageType": "HI"})
    by_name = Navaid(ident="JFK", usage_type="HI")

    assert by_wire.usage_type == by_name.usage_type == "HI"
    assert by_wire.model_dump(by_alias=True)["usageType"] == "HI"
    # A missing key stays None rather than blowing up.
    assert Navaid.model_validate({"ident": "JFK"}).usage_type is None


def test_list_with_every_filter(navaids: Navaids, respx_mock: respx.MockRouter) -> None:
    payload = {
        **PAYLOAD,
        "total": 431,
        "filters_applied": {
            "ident": "JF",
            "airport": "KJFK",
            "type": "VOR-DME",
            "country": "US",
            "bbox": BBOX_WIRE,
        },
    }
    route = _mock(respx_mock, payload)

    result = navaids.list(
        ident="jf", airport="kjfk", type="vor-dme", country="us", bbox=BBOX, limit=500
    )

    params = route.calls.last.request.url.params
    assert params["ident"] == "jf"
    assert params["airport"] == "kjfk"
    assert params["type"] == "vor-dme"
    assert params["country"] == "us"
    assert params["bbox"] == BBOX_WIRE
    assert params["limit"] == "500"

    # The API upper-cases exact-match filters; the echo shows what it used.
    assert result.filters_applied["airport"] == "KJFK"
    assert result.filters_applied["type"] == "VOR-DME"
    # total counts matches *before* the limit, so it can exceed len(navaids).
    assert result.total == 431
    assert len(result.navaids) == 1


def test_empty_result_and_missing_keys_use_defaults(
    navaids: Navaids, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, {"navaids": [], "total": 0, "filters_applied": {"ident": "ZZZZZ"}})

    result = navaids.list(ident="ZZZZZ")

    assert result.navaids == []
    assert result.total == 0
    assert result.filters_applied == {"ident": "ZZZZZ"}


def test_null_leaves_and_unknown_fields_survive(
    navaids: Navaids, respx_mock: respx.MockRouter
) -> None:
    _mock(
        respx_mock,
        {
            "navaids": [
                {
                    "id": 1,
                    "ident": "AA",
                    "name": None,
                    "type": "NDB",
                    "frequency_khz": None,
                    "usageType": None,
                    "associated_airport": None,
                    "brand_new_field": 7,
                }
            ],
            "total": 1,
            "filters_applied": {"type": "NDB"},
        },
    )

    navaid = navaids.list(type="NDB").navaids[0]

    assert navaid.frequency_khz is None
    assert navaid.usage_type is None
    assert navaid.model_extra is not None
    assert navaid.model_extra["brand_new_field"] == 7


def test_inverted_bbox_is_a_400(navaids: Navaids, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/navaids").mock(
        return_value=httpx.Response(400, json={"detail": "Invalid bbox: lat1 must be <= lat2"})
    )

    with pytest.raises(BadRequestError) as excinfo:
        navaids.list(bbox=(42.0, -73.0, 40.0, -80.0))

    assert excinfo.value.status_code == 400


def test_request_options_are_forwarded(navaids: Navaids, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, PAYLOAD)

    navaids.list(airport="KJFK", request_options={"headers": {"X-Trace": "abc"}})

    assert route.calls.last.request.headers["X-Trace"] == "abc"


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_list(async_navaids: AsyncNavaids, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, PAYLOAD)

    result = await async_navaids.list(bbox=BBOX, type="VOR-DME")

    params = route.calls.last.request.url.params
    assert params["bbox"] == BBOX_WIRE
    assert params["type"] == "VOR-DME"
    assert result.navaids[0].usage_type == "BOTH"


async def test_async_list_validates_before_sending(
    async_navaids: AsyncNavaids, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, PAYLOAD)

    with pytest.raises(ValueError, match="at least one filter"):
        await async_navaids.list()

    assert route.call_count == 0
