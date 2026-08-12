"""Error hierarchy and the parser for the three error body shapes."""

from __future__ import annotations

import httpx
import pytest
import respx

from conftest import SleepRecorder
from skylink_api._client import SkyLink
from skylink_api._exceptions import (
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    SkyLinkError,
    UnprocessableEntityError,
    make_status_error,
    parse_error_body,
)
from skylink_api._response import RateLimitInfo

# Shape A — the gateway's 401 envelope (main.py:_json_401).
GATEWAY_401 = {
    "error": "Unauthorized",
    "message": "Access to SkyLink API is available exclusively through api.market and RapidAPI.",
    "code": "MARKETPLACE_ACCESS_REQUIRED",
}
# Shape B — any HTTPException.
DETAIL_404 = {"detail": "Airport not found: KZZZ"}
# Shape C — FastAPI request validation.
VALIDATION_422 = {
    "detail": [
        {
            "loc": ["query", "icao"],
            "msg": "String should have at least 4 characters",
            "type": "string_too_short",
        },
        {
            "loc": ["query", "limit"],
            "msg": "Input should be less than or equal to 500",
            "type": "less_than_equal",
        },
    ]
}


def _client(sleeper: SleepRecorder) -> SkyLink:
    return SkyLink(api_key="k", sleep=sleeper, environ={}, max_retries=0)


def _raise(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder, response: httpx.Response
) -> APIStatusError:
    respx_mock.route().mock(return_value=response)
    with _client(sleeper) as sky, pytest.raises(APIStatusError) as excinfo:
        sky.request("GET", "/health")
    return excinfo.value


# ── the three body shapes ────────────────────────────────────────────────────


def test_shape_a_gateway_envelope(respx_mock: respx.MockRouter, sleeper: SleepRecorder) -> None:
    error = _raise(respx_mock, sleeper, httpx.Response(401, json=GATEWAY_401))

    assert isinstance(error, AuthenticationError)
    assert error.status_code == 401
    assert error.message == GATEWAY_401["message"]
    assert error.code == "MARKETPLACE_ACCESS_REQUIRED"
    assert error.body == GATEWAY_401


def test_shape_b_http_exception_detail(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    error = _raise(respx_mock, sleeper, httpx.Response(404, json=DETAIL_404))

    assert isinstance(error, NotFoundError)
    assert error.message == "Airport not found: KZZZ"
    assert error.code is None
    assert error.errors == ()


def test_shape_c_validation_errors(respx_mock: respx.MockRouter, sleeper: SleepRecorder) -> None:
    error = _raise(respx_mock, sleeper, httpx.Response(422, json=VALIDATION_422))

    assert isinstance(error, UnprocessableEntityError)
    assert len(error.errors) == 2
    first = error.errors[0]
    assert first.loc == ("query", "icao")
    assert first.msg == "String should have at least 4 characters"
    assert first.type == "string_too_short"
    assert "query.icao" in error.message


def test_error_shapes_via_parser_directly() -> None:
    assert parse_error_body(GATEWAY_401, status_code=401).code == "MARKETPLACE_ACCESS_REQUIRED"
    assert parse_error_body(DETAIL_404, status_code=404).message == "Airport not found: KZZZ"
    assert len(parse_error_body(VALIDATION_422, status_code=422).errors) == 2


def test_non_json_body_falls_back_to_text(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    error = _raise(respx_mock, sleeper, httpx.Response(502, text="<html>Bad Gateway</html>"))

    assert isinstance(error, InternalServerError)
    assert error.status_code == 502
    assert "Bad Gateway" in error.message
    assert error.body == "<html>Bad Gateway</html>"


def test_empty_body_gets_generic_message(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    error = _raise(respx_mock, sleeper, httpx.Response(500))

    assert error.message == "HTTP 500"


def test_unknown_dict_shape_gets_generic_message() -> None:
    parsed = parse_error_body({"weird": True}, status_code=418)
    assert parsed.message == "HTTP 418"
    assert parsed.code is None


def test_empty_validation_list_does_not_claim_errors() -> None:
    parsed = parse_error_body({"detail": []}, status_code=422)
    assert parsed.errors == ()
    assert parsed.message == "HTTP 422"


# ── status → class mapping ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, BadRequestError),
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, NotFoundError),
        (422, UnprocessableEntityError),
        (429, RateLimitError),
        (500, InternalServerError),
        (503, ServiceUnavailableError),
    ],
)
def test_status_maps_to_class(
    status: int,
    expected: type[APIStatusError],
    respx_mock: respx.MockRouter,
    sleeper: SleepRecorder,
) -> None:
    error = _raise(respx_mock, sleeper, httpx.Response(status, json={"detail": "nope"}))
    assert type(error) is expected
    assert isinstance(error, SkyLinkError)


def test_service_unavailable_is_an_internal_server_error() -> None:
    error = make_status_error(503, body={"detail": "history database unavailable"})
    assert isinstance(error, ServiceUnavailableError)
    assert isinstance(error, InternalServerError)


def test_unmapped_5xx_becomes_internal_server_error() -> None:
    assert isinstance(make_status_error(504, body=None), InternalServerError)


def test_unmapped_4xx_stays_generic() -> None:
    error = make_status_error(418, body={"detail": "teapot"})
    assert type(error) is APIStatusError
    assert error.status_code == 418


def test_headers_are_exposed_on_the_error(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    error = _raise(
        respx_mock,
        sleeper,
        httpx.Response(404, headers={"X-Request-Id": "req_42"}, json=DETAIL_404),
    )
    assert error.headers["x-request-id"] == "req_42"


# ── rate limiting ────────────────────────────────────────────────────────────


def test_rate_limit_error_carries_quota_and_retry_after(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    error = _raise(
        respx_mock,
        sleeper,
        httpx.Response(
            429,
            headers={
                "X-RateLimit-Requests-Limit": "1000",
                "X-RateLimit-Requests-Remaining": "0",
                "X-RateLimit-Requests-Reset": "3600",
                "Retry-After": "30",
            },
            json={"detail": "Too many requests"},
        ),
    )

    assert isinstance(error, RateLimitError)
    assert error.retry_after == 30.0
    assert error.rate_limit == RateLimitInfo(limit=1000, remaining=0, reset=3600)


def test_rate_limit_error_without_quota_headers(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    error = _raise(respx_mock, sleeper, httpx.Response(429, json={"detail": "slow down"}))
    assert isinstance(error, RateLimitError)
    assert error.rate_limit is None
    assert error.retry_after is None


def test_last_rate_limit_updates_on_success(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    respx_mock.route().mock(
        return_value=httpx.Response(
            200,
            headers={
                "X-RateLimit-Requests-Limit": "10000",
                "X-RateLimit-Requests-Remaining": "9987",
                "X-RateLimit-Requests-Reset": "1200",
            },
            json={"ok": True},
        )
    )
    with _client(sleeper) as sky:
        assert sky.last_rate_limit is None
        sky.request("GET", "/health")
        assert sky.last_rate_limit == RateLimitInfo(limit=10000, remaining=9987, reset=1200)


def test_last_rate_limit_stays_none_without_headers(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    respx_mock.route().mock(return_value=httpx.Response(200, json={"ok": True}))
    with _client(sleeper) as sky:
        sky.request("GET", "/health")
        assert sky.last_rate_limit is None


# ── sentinel responses are NOT errors ────────────────────────────────────────


def test_found_false_sentinel_is_returned_not_raised(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    """``/aircraft/registration/{reg}`` answers 200 ``{found: false}`` instead of 404."""

    payload = {"query": "N-NONE", "found": False, "aircraft": None}
    respx_mock.route().mock(return_value=httpx.Response(200, json=payload))
    with _client(sleeper) as sky:
        assert sky.request("GET", "/aircraft/registration/N-NONE") == payload


def test_error_key_inside_200_is_not_raised(
    respx_mock: respx.MockRouter, sleeper: SleepRecorder
) -> None:
    """``/airports/search/ip`` reports geolocation failure inside a 200 body."""

    payload = {"ip_address": "127.0.0.1", "airports": [], "error": "Private IP address"}
    respx_mock.route().mock(return_value=httpx.Response(200, json=payload))
    with _client(sleeper) as sky:
        assert sky.request("GET", "/airports/search/ip")["error"] == "Private IP address"
