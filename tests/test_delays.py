"""``sky.delays`` — live FAA NAS status.

No recorded fixture: the payload below is the router's OpenAPI example
(``routers/v3/delays.py``) reproduced inline, which is where the prose durations
(``"1 hour and 30 minutes"``) come from.

The namespace is not attached to the client yet (task A8 does the wiring), so
the tests instantiate ``Delays``/``AsyncDelays`` directly.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import ServiceUnavailableError
from skylink_api.models.delays import FaaDelayResponse
from skylink_api.resources.delays import AsyncDelays, Delays, _faa_spec

PAYLOAD: dict[str, Any] = {
    "ground_delays": [
        {
            "airport": "KEWR",
            "airport_name": None,
            "reason": "WEATHER / THUNDERSTORMS",
            "avg_delay": "1 hour and 30 minutes",
            "max_delay": "2 hours",
        }
    ],
    "ground_stops": [
        {
            "airport": "KLGA",
            "airport_name": "LaGuardia",
            "reason": "WEATHER / LOW CEILINGS",
            "end_time": "9:59 pm EST",
        }
    ],
    "closures": [
        {
            "airport": "KDCA",
            "airport_name": None,
            "reason": "RUNWAY MAINTENANCE",
            "begin": "10:00 pm EST",
            "reopen": "5:00 am EST",
        }
    ],
    "airspace_flow_programs": [
        {
            "facility": "ZNY",
            "reason": "WEATHER / THUNDERSTORMS",
            "fca_start": "3:00 pm EST",
            "fca_end": "11:00 pm EST",
        }
    ],
    "total_alerts": 4,
    "message": None,
}

QUIET_PAYLOAD: dict[str, Any] = {
    "ground_delays": [],
    "ground_stops": [],
    "closures": [],
    "airspace_flow_programs": [],
    "total_alerts": 0,
    "message": "No active FAA delays for KJFK",
}


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    """Mock ``GET {base_url}{path}`` with a JSON body."""

    return respx_mock.get(url=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


@pytest.fixture
def delays(client: SkyLink) -> Delays:
    return Delays(client)


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builder_switches_path_on_the_icao_argument() -> None:
    nationwide = _faa_spec()
    assert (nationwide.method, nationwide.path) == ("GET", "/delays/faa")
    assert nationwide.query is None
    assert nationwide.cast_to is FaaDelayResponse

    # The airport goes in the PATH, not in a query parameter.
    per_airport = _faa_spec("KJFK")
    assert per_airport.path == "/delays/faa/KJFK"
    assert per_airport.query is None
    assert per_airport.cast_to is FaaDelayResponse


# ── nationwide ───────────────────────────────────────────────────────────────


def test_faa_nationwide(delays: Delays, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/delays/faa", PAYLOAD)

    result = delays.faa()

    request = route.calls.last.request
    assert request.url.path == "/v3.1/delays/faa"
    assert request.headers["x-api-key"] == "test-key"

    assert isinstance(result, FaaDelayResponse)
    assert result.total_alerts == 4
    # message is present ONLY when there are no delays.
    assert result.message is None

    delay = result.ground_delays[0]
    assert delay.airport == "KEWR"
    assert delay.airport_name is None
    assert delay.reason == "WEATHER / THUNDERSTORMS"
    # Durations are PROSE, never numbers or timedeltas.
    assert delay.avg_delay == "1 hour and 30 minutes"
    assert isinstance(delay.avg_delay, str)
    assert delay.max_delay == "2 hours"

    stop = result.ground_stops[0]
    assert stop.airport == "KLGA"
    # Times are printed strings, not datetimes.
    assert stop.end_time == "9:59 pm EST"
    assert isinstance(stop.end_time, str)

    closure = result.closures[0]
    assert (closure.begin, closure.reopen) == ("10:00 pm EST", "5:00 am EST")

    afp = result.airspace_flow_programs[0]
    # Flow programs are keyed by ATC facility, not by airport.
    assert afp.facility == "ZNY"
    assert afp.fca_start == "3:00 pm EST"


def test_faa_all_arrays_default_to_empty(delays: Delays, respx_mock: respx.MockRouter) -> None:
    """A payload with none of the four keys still parses into four empty lists."""

    _mock(respx_mock, "/delays/faa", {})

    result = delays.faa()

    assert result.ground_delays == []
    assert result.ground_stops == []
    assert result.closures == []
    assert result.airspace_flow_programs == []
    assert result.total_alerts == 0
    assert result.message is None


# ── per airport ──────────────────────────────────────────────────────────────


def test_faa_for_airport_uses_the_path_variant(
    delays: Delays, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/delays/faa/KEWR", PAYLOAD)

    result = delays.faa("KEWR")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/delays/faa/KEWR"
    assert "icao" not in request.url.params
    assert result.ground_delays[0].airport == "KEWR"


def test_faa_for_airport_keeps_unfiltered_flow_programs(
    delays: Delays, respx_mock: respx.MockRouter
) -> None:
    """Flow programs are facility-level, so the per-airport route returns them all."""

    _mock(
        respx_mock,
        "/delays/faa/KJFK",
        {
            "ground_delays": [],
            "ground_stops": [],
            "closures": [],
            "airspace_flow_programs": PAYLOAD["airspace_flow_programs"],
            "total_alerts": 1,
            "message": None,
        },
    )

    result = delays.faa("KJFK")

    assert result.ground_delays == []
    assert len(result.airspace_flow_programs) == 1
    assert result.airspace_flow_programs[0].facility == "ZNY"
    # ...and they still count towards total_alerts even though no JFK-specific
    # delay exists.
    assert result.total_alerts == 1


def test_faa_quiet_airspace_carries_a_message(delays: Delays, respx_mock: respx.MockRouter) -> None:
    """No delays is a normal 200 with a human-readable message, not a 404."""

    _mock(respx_mock, "/delays/faa/KJFK", QUIET_PAYLOAD)

    result = delays.faa("KJFK")

    assert result.total_alerts == 0
    assert result.message == "No active FAA delays for KJFK"


def test_faa_unknown_fields_survive(delays: Delays, respx_mock: respx.MockRouter) -> None:
    _mock(respx_mock, "/delays/faa", {**PAYLOAD, "arrival_delays": []})

    result = delays.faa()

    assert result.model_extra is not None
    assert result.model_extra["arrival_delays"] == []


def test_faa_503_raises(delays: Delays, respx_mock: respx.MockRouter, sleeper: Any) -> None:
    """The FAA feed being down is a retryable 503."""

    respx_mock.get(url=f"{TEST_BASE_URL}/delays/faa").mock(
        return_value=httpx.Response(
            503, json={"detail": "FAA delay service temporarily unavailable"}
        )
    )

    with pytest.raises(ServiceUnavailableError) as excinfo:
        delays.faa()

    assert excinfo.value.status_code == 503
    # 503 is on the retry list: 1 initial call + 3 retries.
    assert sleeper.count == 3


def test_faa_request_options_are_forwarded(delays: Delays, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(url__startswith=f"{TEST_BASE_URL}/delays/faa").mock(
        return_value=httpx.Response(200, json=PAYLOAD)
    )

    delays.faa(request_options={"headers": {"X-Trace": "abc"}, "query": {"debug": True}})

    request = route.calls.last.request
    assert request.headers["X-Trace"] == "abc"
    assert request.url.params["debug"] == "true"


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_faa_both_paths(
    async_client: AsyncSkyLink, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, "/delays/faa", PAYLOAD)
    _mock(respx_mock, "/delays/faa/KJFK", QUIET_PAYLOAD)

    delays = AsyncDelays(async_client)

    nationwide = await delays.faa()
    jfk = await delays.faa("KJFK")

    assert nationwide.total_alerts == 4
    assert nationwide.ground_delays[0].avg_delay == "1 hour and 30 minutes"
    assert jfk.total_alerts == 0
    assert jfk.message == "No active FAA delays for KJFK"
