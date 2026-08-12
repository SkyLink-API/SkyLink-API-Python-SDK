"""``sky.briefing`` — structured/rendered briefings and the PDF download.

Both JSON fixtures are verbatim from the router's OpenAPI ``examples`` block
(``routers/v3/flight_briefing.py``). The PDF body is synthesised: the router
streams whatever the renderer produced, so the only stable contract is the
``%PDF`` magic prefix.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import BadRequestError, NotFoundError, UnprocessableEntityError
from skylink_api.models.briefing import AirportBriefing, FlightBriefing, FlightBriefingText
from skylink_api.resources.briefing import AsyncBriefing, Briefing, _flight_spec, _pdf_spec

#: A minimal but real PDF header — enough for the magic-bytes check.
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


@pytest.fixture
def briefing(client: SkyLink) -> Briefing:
    """The namespace, built directly — wiring onto the client is task A8."""

    return Briefing(client)


@pytest.fixture
def async_briefing(async_client: AsyncSkyLink) -> AsyncBriefing:
    return AsyncBriefing(async_client)


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builders_produce_the_documented_specs() -> None:
    spec = _flight_spec(origin="KJFK", destination="EGLL")
    assert spec.method == "GET"
    assert spec.path == "/briefing/flight"
    assert spec.response_kind == "json"
    assert spec.query == {
        "origin": "KJFK",
        "destination": "EGLL",
        "include_weather": True,
        "include_notams": True,
        "include_pireps": False,
        "format": "json",
    }
    # cast_to follows format: the text variants are still JSON on the wire.
    assert spec.cast_to is FlightBriefing
    assert _flight_spec(origin="KJFK", destination="EGLL", format="html").cast_to is (
        FlightBriefingText
    )
    assert _flight_spec(origin="KJFK", destination="EGLL", format="markdown").cast_to is (
        FlightBriefingText
    )

    pdf = _pdf_spec(departure_icao="KJFK", arrival_icao="EGLL")
    assert pdf.path == "/briefing/pdf"
    assert pdf.response_kind == "bytes"
    assert pdf.cast_to is None
    assert pdf.query == {
        "departure_icao": "KJFK",
        "arrival_icao": "EGLL",
        "flight_number": None,
    }


# ── flight (json) ────────────────────────────────────────────────────────────


def test_flight_json_happy_path(briefing: Briefing, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/briefing/flight", load_fixture("briefing_flight"))

    result = briefing.flight(origin="KJFK", destination="EGLL")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/briefing/flight"
    assert request.url.params["origin"] == "KJFK"
    assert request.url.params["destination"] == "EGLL"
    assert request.url.params["format"] == "json"
    assert request.url.params["include_weather"] == "true"
    assert request.url.params["include_notams"] == "true"
    assert request.url.params["include_pireps"] == "false"

    assert isinstance(result, FlightBriefing)
    assert result.origin == "KJFK"
    assert result.summary is not None
    assert result.critical_restrictions == []
    assert result.data_included == ["metar", "taf", "notams"]

    origin = result.origin_briefing
    assert isinstance(origin, AirportBriefing)
    assert origin.weather is not None
    assert origin.weather.metar_raw is not None
    assert origin.weather.metar_raw.startswith("KJFK ")
    assert origin.notams is not None
    assert origin.notams[0].title == "Taxiway B closed"
    assert origin.notams[0].affected == "TWY B"


def test_flight_json_nullable_lists_keep_none_apart_from_empty(
    briefing: Briefing, respx_mock: respx.MockRouter
) -> None:
    """``None`` = source excluded; ``[]`` = source consulted, nothing found."""

    _mock(respx_mock, "/briefing/flight", load_fixture("briefing_flight"))

    result = briefing.flight(origin="KJFK", destination="EGLL")

    assert result.origin_briefing is not None
    assert result.destination_briefing is not None
    # include_pireps defaults to False → the key is null, not an empty list.
    assert result.origin_briefing.pireps is None
    assert result.destination_briefing.pireps is None
    # NOTAMs were requested: KJFK has one, EGLL genuinely has none.
    assert result.origin_briefing.notams is not None
    assert len(result.origin_briefing.notams) == 1
    assert result.destination_briefing.notams == []


def test_flight_json_include_flags_are_forwarded(
    briefing: Briefing, respx_mock: respx.MockRouter
) -> None:
    payload = {
        **load_fixture("briefing_flight"),
        "data_included": ["pireps"],
        "origin_briefing": {"icao": "KJFK", "weather": None, "notams": None, "pireps": []},
    }
    route = _mock(respx_mock, "/briefing/flight", payload)

    result = briefing.flight(
        origin="KJFK",
        destination="EGLL",
        include_weather=False,
        include_notams=False,
        include_pireps=True,
    )

    params = route.calls.last.request.url.params
    assert params["include_weather"] == "false"
    assert params["include_notams"] == "false"
    assert params["include_pireps"] == "true"

    assert result.origin_briefing is not None
    assert result.origin_briefing.weather is None
    assert result.origin_briefing.notams is None
    assert result.origin_briefing.pireps == []


def test_flight_json_survives_unknown_fields(
    briefing: Briefing, respx_mock: respx.MockRouter
) -> None:
    payload = {**load_fixture("briefing_flight"), "model_version": "gpt-next"}
    _mock(respx_mock, "/briefing/flight", payload)

    result = briefing.flight(origin="KJFK", destination="EGLL")

    assert result.model_extra is not None
    assert result.model_extra["model_version"] == "gpt-next"


def test_flight_no_data_source_is_a_400(briefing: Briefing, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/briefing/flight").mock(
        return_value=httpx.Response(
            400,
            json={
                "detail": "At least one data source (weather, NOTAMs, or PIREPs) must be included"
            },
        )
    )

    with pytest.raises(BadRequestError) as excinfo:
        briefing.flight(
            origin="KJFK",
            destination="EGLL",
            include_weather=False,
            include_notams=False,
            include_pireps=False,
        )

    assert excinfo.value.status_code == 400


def test_flight_unknown_airport_is_a_404(briefing: Briefing, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/briefing/flight").mock(
        return_value=httpx.Response(
            404, json={"detail": "Origin airport not found for ICAO code: ZZZZ"}
        )
    )

    with pytest.raises(NotFoundError):
        briefing.flight(origin="ZZZZ", destination="EGLL")


# ── flight (text formats) ────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", ["markdown", "plain_text", "html"])
def test_flight_text_formats_return_a_bare_string(
    briefing: Briefing, respx_mock: respx.MockRouter, fmt: str
) -> None:
    """The wire body is an envelope; the method contract is a plain ``str``."""

    fixture = {**load_fixture("briefing_text"), "format": fmt}
    route = _mock(respx_mock, "/briefing/flight", fixture)

    result = briefing.flight(origin="KJFK", destination="EGLL", format=fmt)  # type: ignore[arg-type]

    assert route.calls.last.request.url.params["format"] == fmt
    assert isinstance(result, str)
    assert not isinstance(result, FlightBriefingText)
    assert result == fixture["briefing"]
    assert result.startswith("<h2>Summary</h2>")


def test_flight_text_missing_briefing_key_still_yields_a_string(
    briefing: Briefing, respx_mock: respx.MockRouter
) -> None:
    payload = {k: v for k, v in load_fixture("briefing_text").items() if k != "briefing"}
    _mock(respx_mock, "/briefing/flight", payload)

    result = briefing.flight(origin="KJFK", destination="EGLL", format="markdown")

    assert result == ""


def test_flight_request_options_are_forwarded(
    briefing: Briefing, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/briefing/flight", load_fixture("briefing_flight"))

    briefing.flight(
        origin="KJFK",
        destination="EGLL",
        request_options={"headers": {"X-Trace": "abc"}, "query": {"debug": True}},
    )

    request = route.calls.last.request
    assert request.headers["X-Trace"] == "abc"
    assert request.url.params["debug"] == "true"


# ── pdf ──────────────────────────────────────────────────────────────────────


def test_pdf_returns_raw_bytes(briefing: Briefing, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/briefing/pdf").mock(
        return_value=httpx.Response(
            200,
            content=PDF_BYTES,
            headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": 'attachment; filename="skylink_briefing_KJFK_EGLL.pdf"',
            },
        )
    )

    result = briefing.pdf(departure_icao="KJFK", arrival_icao="EGLL", flight_number="BA117")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/briefing/pdf"
    assert request.url.params["departure_icao"] == "KJFK"
    assert request.url.params["arrival_icao"] == "EGLL"
    assert request.url.params["flight_number"] == "BA117"
    # response_kind="bytes" asks for anything, not just JSON.
    assert request.headers["accept"] == "*/*"

    assert isinstance(result, bytes)
    assert result.startswith(b"%PDF")
    assert result == PDF_BYTES


def test_pdf_omits_optional_flight_number(briefing: Briefing, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/briefing/pdf").mock(
        return_value=httpx.Response(200, content=PDF_BYTES)
    )

    briefing.pdf(departure_icao="KJFK", arrival_icao="EGLL")

    assert "flight_number" not in route.calls.last.request.url.params


def test_pdf_same_airports_is_a_422(briefing: Briefing, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/briefing/pdf").mock(
        return_value=httpx.Response(
            422, json={"detail": "Departure and arrival airports must be different"}
        )
    )

    with pytest.raises(UnprocessableEntityError) as excinfo:
        briefing.pdf(departure_icao="KJFK", arrival_icao="KJFK")

    assert excinfo.value.status_code == 422


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_flight_json(
    async_briefing: AsyncBriefing, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, "/briefing/flight", load_fixture("briefing_flight"))

    result = await async_briefing.flight(origin="KJFK", destination="EGLL")

    assert isinstance(result, FlightBriefing)
    assert result.destination == "EGLL"
    assert result.destination_briefing is not None
    assert result.destination_briefing.pireps is None


async def test_async_flight_text_and_pdf(
    async_briefing: AsyncBriefing, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, "/briefing/flight", load_fixture("briefing_text"))
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/briefing/pdf").mock(
        return_value=httpx.Response(200, content=PDF_BYTES)
    )

    text = await async_briefing.flight(origin="KJFK", destination="EGLL", format="markdown")
    pdf = await async_briefing.pdf(departure_icao="KJFK", arrival_icao="EGLL")

    assert isinstance(text, str)
    assert text.startswith("<h2>Summary</h2>")
    assert pdf.startswith(b"%PDF")
