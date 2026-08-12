"""Query serialisation, date formatters and response processing."""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest
import respx
from pydantic import BaseModel

from conftest import SleepRecorder
from skylink_api._client import SkyLink
from skylink_api._exceptions import APIResponseValidationError
from skylink_api._qs import (
    build_query,
    format_bbox,
    format_history_datetime,
    format_query_value,
    format_schedule_date,
    format_ticket_date,
)
from skylink_api._response import (
    RateLimitInfo,
    parse_rate_limit,
    parse_retry_after,
    process_response,
)
from skylink_api._types import NOT_GIVEN, RequestSpec


class Airline(BaseModel):
    id: int
    name: str
    iata: str | None = None


# ── query values ─────────────────────────────────────────────────────────────


def test_none_and_not_given_are_dropped() -> None:
    assert build_query({"a": 1, "b": None, "c": NOT_GIVEN}) == {"a": "1"}


def test_booleans_are_lowercase_strings() -> None:
    # FastAPI rejects Python's "True"/"False".
    assert build_query({"parsed": True, "photos": False}) == {"parsed": "true", "photos": "false"}


def test_numbers_and_strings() -> None:
    assert build_query({"limit": 100, "radius": 12.5, "icao": "KJFK"}) == {
        "limit": "100",
        "radius": "12.5",
        "icao": "KJFK",
    }


def test_zero_and_empty_string_are_kept() -> None:
    assert build_query({"offset": 0, "q": ""}) == {"offset": "0", "q": ""}


def test_bbox_tuple_becomes_a_single_string() -> None:
    assert build_query({"bbox": (40.0, -74.5, 41.0, -73.5)}) == {"bbox": "40.0,-74.5,41.0,-73.5"}


def test_format_bbox_helper() -> None:
    assert format_bbox((40.0, -74.5, 41.0, -73.5)) == "40.0,-74.5,41.0,-73.5"
    assert format_bbox([40, -74, 41, -73]) == "40,-74,41,-73"
    assert format_bbox("40,-74,41,-73") == "40,-74,41,-73"
    with pytest.raises(ValueError, match="bbox must have 4 values"):
        format_bbox((1.0, 2.0))


def test_lists_become_csv() -> None:
    # exclude_qcode / exclude_scope are comma separated on the wire.
    assert build_query({"exclude_scope": ["AERODROME", "FIR"]}) == {
        "exclude_scope": "AERODROME,FIR"
    }
    assert build_query({"exclude_qcode": []}) == {}


def test_datetimes_default_to_iso() -> None:
    moment = datetime(2026, 8, 12, 6, 30, tzinfo=timezone.utc)
    assert build_query({"start": moment})["start"] == "2026-08-12T06:30:00+00:00"
    assert build_query({"day": date(2026, 8, 12)})["day"] == "2026-08-12"


def test_later_sources_win_and_none_removes() -> None:
    assert build_query({"limit": 10, "photos": True}, {"limit": 50}) == {
        "limit": "50",
        "photos": "true",
    }
    assert build_query({"limit": 10}, {"limit": None}) == {}


def test_mapping_values_are_rejected() -> None:
    with pytest.raises(TypeError):
        format_query_value({"nested": 1})


# ── per-endpoint date formats ────────────────────────────────────────────────


def test_schedules_want_dd_mm_yyyy() -> None:
    assert format_schedule_date(date(2026, 2, 5)) == "05-02-2026"
    assert format_schedule_date(datetime(2026, 2, 5, 10, 30)) == "05-02-2026"
    assert format_schedule_date("2026-02-05") == "05-02-2026"
    # Already in the endpoint's format — passed through untouched.
    assert format_schedule_date("05-02-2026") == "05-02-2026"


def test_tickets_want_yyyy_mm_dd() -> None:
    assert format_ticket_date(date(2026, 2, 5)) == "2026-02-05"
    assert format_ticket_date(datetime(2026, 2, 5, 10, 30)) == "2026-02-05"
    assert format_ticket_date("2026-02-05") == "2026-02-05"


def test_history_wants_iso_8601() -> None:
    moment = datetime(2026, 8, 12, 6, 30, 15, tzinfo=timezone.utc)
    assert format_history_datetime(moment) == "2026-08-12T06:30:15+00:00"
    assert format_history_datetime(date(2026, 8, 12)) == "2026-08-12"
    assert format_history_datetime("2026-08-12T06:30:15Z") == "2026-08-12T06:30:15Z"


# ── response processing ──────────────────────────────────────────────────────


def _spec(**kwargs: object) -> RequestSpec:
    base: dict[str, object] = {"method": "GET", "path": "/x"}
    base.update(kwargs)
    return RequestSpec(**base)  # type: ignore[arg-type]


def test_json_without_cast_returns_plain_data() -> None:
    response = httpx.Response(200, json={"a": 1})
    assert process_response(_spec(), response) == {"a": 1}


def test_json_cast_to_model() -> None:
    response = httpx.Response(200, json={"id": 1, "name": "British Airways", "iata": "BA"})
    airline = process_response(_spec(cast_to=Airline), response)
    assert isinstance(airline, Airline)
    assert airline.name == "British Airways"


def test_json_cast_to_list_of_models() -> None:
    response = httpx.Response(200, json=[{"id": 1, "name": "BA"}, {"id": 2, "name": "AA"}])
    airlines = process_response(_spec(cast_to=list[Airline]), response)
    assert [airline.id for airline in airlines] == [1, 2]


def test_cast_failure_raises_response_validation_error() -> None:
    response = httpx.Response(200, json={"id": "not-an-int", "name": "BA"})
    with pytest.raises(APIResponseValidationError) as excinfo:
        process_response(_spec(cast_to=Airline), response)
    assert excinfo.value.status_code == 200


def test_invalid_json_raises_response_validation_error() -> None:
    response = httpx.Response(200, text="<html>oops</html>")
    with pytest.raises(APIResponseValidationError):
        process_response(_spec(), response)


def test_bytes_kind_returns_raw_content() -> None:
    response = httpx.Response(200, content=b"%PDF-1.4 ...")
    assert process_response(_spec(response_kind="bytes"), response) == b"%PDF-1.4 ..."


def test_text_kind_returns_str() -> None:
    response = httpx.Response(200, text="ORIGIN KJFK ...")
    assert process_response(_spec(response_kind="text"), response) == "ORIGIN KJFK ..."


def test_none_kind_returns_none() -> None:
    """DELETE /webhooks/{id} answers 204 with no body."""

    response = httpx.Response(204)
    assert process_response(_spec(response_kind="none"), response) is None


def test_empty_json_body_returns_none() -> None:
    assert process_response(_spec(cast_to=Airline), httpx.Response(200)) is None


# ── rate limit headers ───────────────────────────────────────────────────────


def test_rate_limit_headers_are_parsed() -> None:
    headers = {
        "X-RateLimit-Requests-Limit": "1000",
        "X-RateLimit-Requests-Remaining": "997",
        "X-RateLimit-Requests-Reset": "86400",
    }
    assert parse_rate_limit(headers) == RateLimitInfo(limit=1000, remaining=997, reset=86400)


def test_rate_limit_header_lookup_is_case_insensitive() -> None:
    assert parse_rate_limit({"x-ratelimit-requests-limit": "5"}) == RateLimitInfo(limit=5)


def test_rate_limit_missing_headers() -> None:
    assert parse_rate_limit({}) is None
    assert parse_rate_limit({"Content-Type": "application/json"}) is None


def test_rate_limit_garbage_values_are_ignored() -> None:
    assert parse_rate_limit({"X-RateLimit-Requests-Limit": "n/a"}) is None


def test_retry_after_parsing() -> None:
    assert parse_retry_after({"Retry-After": "5"}) == 5.0
    assert parse_retry_after({"retry-after": "0"}) == 0.0
    assert parse_retry_after({"Retry-After": "9999"}) == 60.0
    assert parse_retry_after({"Retry-After": "not-a-date"}) is None
    assert parse_retry_after({}) is None


# ── end to end through the client ────────────────────────────────────────────


def test_query_is_serialised_on_the_wire(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    route = respx_mock.route().mock(return_value=httpx.Response(200, json={"ok": True}))
    with SkyLink(api_key="k", sleep=sleeper, environ={}) as sky:
        sky.request(
            "GET",
            "/adsb/aircraft",
            query={
                "bbox": (40.0, -74.5, 41.0, -73.5),
                "photos": False,
                "limit": 25,
                "callsign": None,
            },
        )

    params = route.calls.last.request.url.params
    assert params["bbox"] == "40.0,-74.5,41.0,-73.5"
    assert params["photos"] == "false"
    assert params["limit"] == "25"
    assert "callsign" not in params


def test_per_request_query_is_merged(respx_mock: respx.MockRouter, sleeper: SleepRecorder) -> None:
    route = respx_mock.route().mock(return_value=httpx.Response(200, json={"ok": True}))
    with SkyLink(api_key="k", sleep=sleeper, environ={}) as sky:
        sky.request("GET", "/navaids", query={"limit": 10}, options={"query": {"country": "US"}})

    params = route.calls.last.request.url.params
    assert params["limit"] == "10"
    assert params["country"] == "US"


def test_client_casts_into_models(respx_mock: respx.MockRouter, sleeper: SleepRecorder) -> None:
    respx_mock.route().mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "British Airways", "iata": "BA"}])
    )
    with SkyLink(api_key="k", sleep=sleeper, environ={}) as sky:
        airlines = sky.request("GET", "/airlines/search", cast_to=list[Airline])

    assert airlines[0].iata == "BA"
