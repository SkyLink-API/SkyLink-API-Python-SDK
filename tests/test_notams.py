"""``sky.notams`` — Notices to Air Missions.

The namespace is not attached to the client yet (task A8 does the wiring), so the
resource is constructed directly against the client here.

Traps under test: ``effective``/``expiration`` stay opaque strings across all
five formats the feed mixes, ``status`` only carries ``FUTURE`` when
``include_future=True``, and the CSV filters accept both a string and a sequence.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import ServiceUnavailableError
from skylink_api.models.notams import NotamsResponse
from skylink_api.resources.notams import AsyncNotams, Notams, _by_airport_spec


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}{path}`` (query independent) with a JSON body."""

    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builder_produces_the_documented_spec() -> None:
    spec = _by_airport_spec("OMDB")

    assert spec.method == "GET"
    assert spec.path == "/notams/OMDB"
    assert spec.cast_to is NotamsResponse
    assert spec.query == {
        "exclude_qcode": None,
        "exclude_scope": None,
        "include_future": False,
    }


def test_csv_params_accept_strings_and_sequences() -> None:
    """Both spellings must produce the identical comma separated wire value."""

    from_string = _by_airport_spec("KJFK", exclude_qcode="QK,QF", exclude_scope="FIR")
    from_list = _by_airport_spec("KJFK", exclude_qcode=["QK", "QF"], exclude_scope=["FIR"])

    assert from_string.query == from_list.query
    assert from_list.query is not None
    assert from_list.query["exclude_qcode"] == "QK,QF"
    assert from_list.query["exclude_scope"] == "FIR"

    # A tuple of two scopes joins the same way.
    both = _by_airport_spec("KJFK", exclude_scope=("AERODROME", "FIR"))
    assert both.query is not None
    assert both.query["exclude_scope"] == "AERODROME,FIR"

    # An empty sequence means "no filter", not an empty parameter.
    empty = _by_airport_spec("KJFK", exclude_qcode=[])
    assert empty.query is not None
    assert empty.query["exclude_qcode"] is None


# ── happy path ───────────────────────────────────────────────────────────────


def test_by_airport_happy_path(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/notams/OMDB", load_fixture("notams"))

    result = Notams(client).by_airport("OMDB")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/notams/OMDB"
    assert request.url.params["include_future"] == "false"
    assert "exclude_qcode" not in request.url.params
    assert "exclude_scope" not in request.url.params

    assert isinstance(result, NotamsResponse)
    assert result.icao == "OMDB"
    assert result.total == 1

    notam = result.notams[0]
    assert notam.notam_id == "A2161/2026"
    assert notam.notam_id_domestic == "07/2161"
    assert notam.type == "N"
    assert notam.scope == "AERODROME"
    assert notam.body == "RWY 12L/30R CLSD"
    assert notam.qline is not None
    assert notam.qline.startswith("OMAE/QMRLC")
    assert notam.raw is not None
    assert "NOTAMN" in notam.raw
    # Items the originating authority did not file stay None.
    assert notam.schedule is None
    assert notam.q_code is None
    assert notam.status is None


def test_effective_and_expiration_stay_opaque_strings(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    """Never parsed: the feed mixes ISO, 12-digit, 10-digit, EST and PERM."""

    payload = {
        "icao": "KJFK",
        "notams": [
            # Compact 12-digit YYYYMMDDHHMM (the fixture's format).
            {"raw": "A", "effective": "202607162130", "expiration": "202607302215"},
            # Compact 10-digit YYMMDDHHMM (older FAA form).
            {"raw": "B", "effective": "2607162130", "expiration": "2607302215"},
            # ISO 8601 with Z — same field, same response.
            {"raw": "C", "effective": "2026-07-16T21:30:00Z", "expiration": "2026-07-30T22:15:00Z"},
            # Estimated end time and a permanent NOTAM.
            {"raw": "D", "effective": "2607162130", "expiration": "2607302215EST"},
            {"raw": "E", "effective": "202607162130", "expiration": "PERM"},
        ],
        "total": 5,
    }
    _mock(respx_mock, "/notams/KJFK", payload)

    notams = Notams(client).by_airport("KJFK").notams

    for notam in notams:
        assert isinstance(notam.effective, str)
        assert isinstance(notam.expiration, str)

    # Verbatim round trip, no normalisation between formats.
    assert [n.expiration for n in notams] == [
        "202607302215",
        "2607302215",
        "2026-07-30T22:15:00Z",
        "2607302215EST",
        "PERM",
    ]
    assert notams[2].effective == "2026-07-16T21:30:00Z"


def test_include_future_adds_the_status_field(
    client: SkyLink, respx_mock: respx.MockRouter
) -> None:
    payload = {
        "icao": "KJFK",
        "notams": [
            {"raw": "A", "notam_id": "A0001/2026", "status": "ACTIVE"},
            {"raw": "B", "notam_id": "A0002/2026", "status": "FUTURE"},
        ],
        "total": 2,
    }
    route = _mock(respx_mock, "/notams/KJFK", payload)

    result = Notams(client).by_airport("KJFK", include_future=True)

    assert route.calls.last.request.url.params["include_future"] == "true"
    assert [n.status for n in result.notams] == ["ACTIVE", "FUTURE"]


def test_exclude_filters_reach_the_wire(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/notams/KJFK", {"icao": "KJFK", "notams": [], "total": 0})

    Notams(client).by_airport("KJFK", exclude_qcode=["QK", "QF"], exclude_scope="FIR")

    params = route.calls.last.request.url.params
    assert params["exclude_qcode"] == "QK,QF"
    assert params["exclude_scope"] == "FIR"


def test_empty_result_is_a_normal_200(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, "/notams/EGLL", {"icao": "EGLL"})

    result = Notams(client).by_airport("EGLL")

    assert result.notams == []
    assert result.total == 0


def test_unknown_fields_survive(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    _mock(
        respx_mock,
        "/notams/KJFK",
        {"icao": "KJFK", "notams": [{"raw": "A", "traffic": "IV"}], "total": 1},
    )

    notam = Notams(client).by_airport("KJFK").notams[0]

    assert notam.model_extra is not None
    assert notam.model_extra["traffic"] == "IV"


def test_service_unavailable_raises(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/notams/KJFK").mock(
        return_value=httpx.Response(503, json={"detail": "NOTAM service temporarily unavailable"})
    )

    with pytest.raises(ServiceUnavailableError) as excinfo:
        Notams(client).by_airport("KJFK", request_options={"max_retries": 0})

    assert excinfo.value.status_code == 503


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_by_airport(async_client: AsyncSkyLink, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/notams/OMDB", load_fixture("notams"))

    result = await AsyncNotams(async_client).by_airport(
        "OMDB", exclude_scope=["FIR"], include_future=True
    )

    params = route.calls.last.request.url.params
    assert params["exclude_scope"] == "FIR"
    assert params["include_future"] == "true"
    assert result.notams[0].effective == "202607162130"
