"""``sky.poll`` — timed iterators over flight status and the live ADS-B feed.

Nothing here sleeps. The pollers take ``sleep=`` and every test passes the
``SleepRecorder`` from ``conftest``, so the waits are asserted rather than
endured and the whole file runs in milliseconds.

The client used here is built with ``max_retries=0`` on purpose: the transport's
own retry loop would swallow the 429/5xx responses these tests feed in, and it is
the **poller's** recovery that is under test. With retries off, one queued
response equals one poll.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from conftest import (
    TEST_API_KEY,
    TEST_BASE_URL,
    TEST_PROVIDER,
    AsyncSleepRecorder,
    SleepRecorder,
)
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import (
    BadRequestError,
    PermissionDeniedError,
    UnprocessableEntityError,
)
from skylink_api.models.adsb import AdsbAircraftList
from skylink_api.models.flight_status import FlightStatusResponse
from skylink_api.models.poll import AdsbDiff
from skylink_api.resources.poll import (
    TERMINAL_FLIGHT_STATUSES,
    AsyncPoll,
    Poll,
    _diff,
    _has_moved,
    _snapshot,
    _status_fingerprint,
    is_terminal_status,
)

STATUS_URL = f"{TEST_BASE_URL}/flight_status/BA123"
ADSB_URL = f"{TEST_BASE_URL}/adsb/aircraft"


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def poll_client(sleeper: SleepRecorder) -> Iterator[SkyLink]:
    """Client with transport retries disabled — see the module docstring."""

    with SkyLink(
        api_key=TEST_API_KEY,
        provider=TEST_PROVIDER,
        max_retries=0,
        sleep=sleeper,
        environ={},
    ) as sky:
        yield sky


@pytest.fixture
def poll(poll_client: SkyLink) -> Poll:
    return Poll(poll_client)


@pytest.fixture
def waits() -> SleepRecorder:
    """The poller's own ``sleep``, separate from the transport's backoff."""

    return SleepRecorder()


@pytest.fixture
def async_waits() -> AsyncSleepRecorder:
    return AsyncSleepRecorder()


# ── payload helpers ──────────────────────────────────────────────────────────


def status_payload(status: str, **overrides: Any) -> dict[str, Any]:
    """A flight-status body with the two asymmetric legs the API really sends."""

    payload: dict[str, Any] = {
        "flight_number": "BA 123",
        "airline": "British Airways",
        "status": status,
        "departure": {
            "airport": "LHR • London",
            "scheduled_time": "10:30",
            "actual_time": "10:35",
            "terminal": "5",
            "gate": "A12",
        },
        "arrival": {
            "airport": "JFK • New York",
            "scheduled_time": "13:45",
            "estimated_time": "13:50",
            "terminal": "7",
            "gate": "--",
            "baggage": "3",
        },
    }
    payload.update(overrides)
    return payload


def aircraft_payload(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"aircraft": list(rows), "total_count": len(rows)}


def row(
    icao24: str,
    *,
    lat: float | None = 51.5,
    lon: float | None = -0.45,
    altitude: int | None = 36000,
    speed: float | None = 450.0,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "icao24": icao24,
        "callsign": f"CS{icao24[-3:]}",
        "latitude": lat,
        "longitude": lon,
        "altitude": altitude,
        "ground_speed": speed,
        **extra,
    }


def responses(*payloads: Any) -> list[httpx.Response]:
    return [httpx.Response(200, json=payload) for payload in payloads]


# ── pure helpers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status",
    [
        "Landed",
        "landed",
        "LANDED 14:32",
        "Landed - on time",
        "Arrived",
        "Cancelled",
        "CANCELLED",
        "Canceled",  # American spelling — a different string, not a substring
        "Flight canceled by the airline",
        "Diverted to EGKK",
    ],
)
def test_terminal_statuses_match_case_insensitively_by_substring(status: str) -> None:
    """The status is scraped prose, so equality would never fire."""

    assert is_terminal_status(status) is True


@pytest.mark.parametrize(
    "status",
    ["En Route", "Scheduled", "Delayed", "Boarding", "Unknown", "", None, "--"],
)
def test_non_terminal_statuses(status: str | None) -> None:
    assert is_terminal_status(status) is False


def test_terminal_status_constant_covers_both_spellings_of_cancelled() -> None:
    assert TERMINAL_FLIGHT_STATUSES == (
        "landed",
        "arrived",
        "cancelled",
        "canceled",
        "diverted",
    )


def test_status_fingerprint_ignores_extra_and_placeholder_noise() -> None:
    """Only the chosen operational fields count; ``""``/``"--"`` fold to unknown."""

    base = FlightStatusResponse.model_validate(status_payload("En Route", retrieved_at="12:00:01"))
    later = FlightStatusResponse.model_validate(status_payload("En Route", retrieved_at="12:01:07"))
    assert _status_fingerprint(base) == _status_fingerprint(later)

    flapping = status_payload("En Route")
    flapping["arrival"]["gate"] = ""  # was "--"
    assert _status_fingerprint(
        FlightStatusResponse.model_validate(flapping)
    ) == _status_fingerprint(base)

    moved = status_payload("En Route")
    moved["arrival"]["gate"] = "B4"
    assert _status_fingerprint(FlightStatusResponse.model_validate(moved)) != _status_fingerprint(
        base
    )


def test_status_fingerprint_survives_missing_legs() -> None:
    """A source page without an arrival card sends ``{}`` or nothing at all."""

    bare = FlightStatusResponse.model_validate({"status": "Scheduled"})
    empty_legs = FlightStatusResponse.model_validate(
        {"status": "Scheduled", "departure": {}, "arrival": {}}
    )
    assert _status_fingerprint(bare) == _status_fingerprint(empty_legs)


def test_snapshot_drops_aircraft_without_an_address_and_lowercases() -> None:
    page = AdsbAircraftList.model_validate(
        aircraft_payload(row("4CA1FB"), row(""), {"callsign": "NOADDR"})
    )

    indexed = _snapshot(page)

    assert list(indexed) == ["4ca1fb"]


def test_has_moved_compares_position_altitude_and_speed_only() -> None:
    before = AdsbAircraftList.model_validate(aircraft_payload(row("aaaaaa"))).aircraft[0]

    same = AdsbAircraftList.model_validate(
        aircraft_payload(row("aaaaaa", last_seen="2026-02-11T12:00:59"))
    ).aircraft[0]
    assert _has_moved(before, same) is False

    for field, value in (
        ("lat", 51.6),
        ("lon", -0.44),
        ("altitude", 35000),
        ("speed", 451.0),
    ):
        after = AdsbAircraftList.model_validate(
            aircraft_payload(row("aaaaaa", **{field: value}))
        ).aircraft[0]
        assert _has_moved(before, after) is True, field


def test_diff_first_call_reports_everything_as_appeared() -> None:
    page = AdsbAircraftList.model_validate(aircraft_payload(row("aaaaaa"), row("bbbbbb")))

    diff = _diff(None, page)

    assert diff.is_first is True
    assert sorted(diff.snapshot) == ["aaaaaa", "bbbbbb"]
    assert len(diff.appeared) == 2
    assert diff.disappeared == []
    assert diff.updated == []


# ── flight_status ────────────────────────────────────────────────────────────


def test_flight_status_first_poll_is_immediate(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(STATUS_URL).mock(side_effect=responses(status_payload("En Route")))

    iterator = poll.flight_status("BA123", interval=60, sleep=waits)
    first = next(iterator)

    assert isinstance(first, FlightStatusResponse)
    assert first.status == "En Route"
    assert waits.calls == []  # no initial wait — the UI needs a value now
    iterator.close()


def test_flight_status_changes_only_skips_repeats(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(STATUS_URL).mock(
        side_effect=responses(
            status_payload("Scheduled"),
            status_payload("Scheduled", retrieved_at="later"),  # noise only
            status_payload("En Route"),
            status_payload("Landed"),
        )
    )

    seen = list(poll.flight_status("BA123", interval=30, sleep=waits))

    assert [status.status for status in seen] == ["Scheduled", "En Route", "Landed"]
    # Four requests, three of them followed by a wait; the terminal one is not.
    assert waits.calls == [30, 30, 30]


def test_flight_status_changes_only_false_yields_every_poll(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(STATUS_URL).mock(
        side_effect=responses(
            status_payload("Scheduled"),
            status_payload("Scheduled"),
            status_payload("Scheduled"),
        )
    )

    seen = list(
        poll.flight_status("BA123", interval=5, changes_only=False, max_iterations=3, sleep=waits)
    )

    assert [status.status for status in seen] == ["Scheduled"] * 3


def test_flight_status_yields_the_terminal_response_then_stops(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(STATUS_URL).mock(
        side_effect=responses(status_payload("En Route"), status_payload("Landed 14:32"))
    )

    seen = list(poll.flight_status("BA123", interval=60, sleep=waits))

    assert [status.status for status in seen] == ["En Route", "Landed 14:32"]
    assert route.call_count == 2  # nothing polled after the terminal state


def test_flight_status_until_terminal_false_keeps_polling(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(STATUS_URL).mock(
        side_effect=responses(
            status_payload("Landed"),
            status_payload("Landed", **{"arrival": {"baggage": "7"}}),
        )
    )

    seen = list(
        poll.flight_status("BA123", interval=1, until_terminal=False, max_iterations=2, sleep=waits)
    )

    assert route.call_count == 2
    assert len(seen) == 2


def test_flight_status_max_iterations_caps_requests(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(STATUS_URL).mock(
        return_value=httpx.Response(200, json=status_payload("En Route"))
    )

    seen = list(poll.flight_status("BA123", interval=60, max_iterations=3, sleep=waits))

    assert route.call_count == 3
    assert len(seen) == 1  # changes_only: only the first is a change
    assert waits.calls == [60, 60]  # no trailing wait after the last request


def test_flight_status_survives_a_rate_limit_and_honours_retry_after(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    """A 429 mid-poll is a pause, not the end of the loop."""

    respx_mock.get(STATUS_URL).mock(
        side_effect=[
            httpx.Response(200, json=status_payload("En Route")),
            httpx.Response(429, headers={"Retry-After": "7"}, json={"detail": "quota"}),
            httpx.Response(200, json=status_payload("Landed")),
        ]
    )

    seen = list(poll.flight_status("BA123", interval=60, sleep=waits))

    assert [status.status for status in seen] == ["En Route", "Landed"]
    assert waits.calls == [60, 7]  # interval, then the server's Retry-After


def test_flight_status_rate_limit_without_retry_after_falls_back_to_interval(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(STATUS_URL).mock(
        side_effect=[
            httpx.Response(429, json={"detail": "quota"}),
            httpx.Response(200, json=status_payload("Landed")),
        ]
    )

    seen = list(poll.flight_status("BA123", interval=45, sleep=waits))

    assert [status.status for status in seen] == ["Landed"]
    assert waits.calls == [45]


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_flight_status_survives_server_errors(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter, status_code: int
) -> None:
    respx_mock.get(STATUS_URL).mock(
        side_effect=[
            httpx.Response(status_code, json={"detail": "upstream down"}),
            httpx.Response(200, json=status_payload("Landed")),
        ]
    )

    seen = list(poll.flight_status("BA123", interval=20, sleep=waits))

    assert [status.status for status in seen] == ["Landed"]
    assert waits.calls == [20]


def test_flight_status_failures_count_towards_max_iterations(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    """A broken backend must not turn a bounded poll into an unbounded one."""

    route = respx_mock.get(STATUS_URL).mock(
        return_value=httpx.Response(503, json={"detail": "down"})
    )

    seen = list(poll.flight_status("BA123", interval=1, max_iterations=4, sleep=waits))

    assert seen == []
    assert route.call_count == 4


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (403, PermissionDeniedError),
        (422, UnprocessableEntityError),
    ],
)
def test_flight_status_propagates_configuration_errors(
    poll: Poll,
    waits: SleepRecorder,
    respx_mock: respx.MockRouter,
    status_code: int,
    expected: type[Exception],
) -> None:
    """403/422 will not fix themselves; retrying them on a timer only burns quota."""

    respx_mock.get(STATUS_URL).mock(
        return_value=httpx.Response(status_code, json={"detail": "nope"})
    )

    with pytest.raises(expected):
        list(poll.flight_status("BA123", interval=60, sleep=waits))

    assert waits.calls == []


def test_flight_status_forwards_request_options(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(url__startswith=STATUS_URL).mock(
        side_effect=responses(status_payload("Landed"))
    )

    list(poll.flight_status("BA123", sleep=waits, request_options={"headers": {"X-Trace": "t1"}}))

    assert route.calls.last.request.headers["X-Trace"] == "t1"


# ── adsb ─────────────────────────────────────────────────────────────────────


def test_adsb_first_diff_is_the_whole_feed(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=ADSB_URL).mock(
        side_effect=responses(aircraft_payload(row("4ca1fb"), row("a1b2c3")))
    )

    diff = next(iter(poll.adsb(interval=10, max_iterations=1, sleep=waits)))

    assert isinstance(diff, AdsbDiff)
    assert diff.is_first is True
    assert sorted(a.icao24 for a in diff.appeared) == ["4ca1fb", "a1b2c3"]
    assert diff.disappeared == []
    assert diff.updated == []
    assert sorted(diff.snapshot) == ["4ca1fb", "a1b2c3"]
    assert waits.calls == []


def test_adsb_diff_reports_appeared_disappeared_and_updated(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=ADSB_URL).mock(
        side_effect=responses(
            aircraft_payload(row("aaaaaa"), row("bbbbbb"), row("cccccc")),
            aircraft_payload(
                row("aaaaaa"),  # unchanged
                row("bbbbbb", lat=52.0),  # moved
                row("dddddd"),  # new
                # cccccc gone
            ),
        )
    )

    diffs = list(poll.adsb(interval=10, max_iterations=2, sleep=waits))

    assert len(diffs) == 2
    second = diffs[1]
    assert second.is_first is False
    assert [a.icao24 for a in second.appeared] == ["dddddd"]
    assert second.disappeared == ["cccccc"]
    assert [a.icao24 for a in second.updated] == ["bbbbbb"]
    assert sorted(second.snapshot) == ["aaaaaa", "bbbbbb", "dddddd"]
    assert waits.calls == [10]


def test_adsb_unchanged_feed_yields_an_empty_diff(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    """A tick with nothing in it is still information: the feed is alive."""

    respx_mock.get(url__startswith=ADSB_URL).mock(
        side_effect=responses(
            aircraft_payload(row("aaaaaa", last_seen="2026-02-11T12:00:00")),
            aircraft_payload(row("aaaaaa", last_seen="2026-02-11T12:00:12")),
        )
    )

    second = list(poll.adsb(interval=10, max_iterations=2, sleep=waits))[1]

    assert (second.appeared, second.disappeared, second.updated) == ([], [], [])
    assert list(second.snapshot) == ["aaaaaa"]


def test_adsb_address_case_does_not_create_phantom_aircraft(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    """Responses upper-case ``icao24``, queries lower-case it — keys are folded."""

    respx_mock.get(url__startswith=ADSB_URL).mock(
        side_effect=responses(
            aircraft_payload(row("4CA1FB")),
            aircraft_payload(row("4ca1fb")),
        )
    )

    second = list(poll.adsb(interval=10, max_iterations=2, sleep=waits))[1]

    assert second.appeared == []
    assert second.disappeared == []
    assert list(second.snapshot) == ["4ca1fb"]


def test_adsb_empty_feed_is_not_an_error(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=ADSB_URL).mock(
        side_effect=responses(aircraft_payload(row("aaaaaa")), aircraft_payload())
    )

    diffs = list(poll.adsb(interval=10, max_iterations=2, sleep=waits))

    assert diffs[1].disappeared == ["aaaaaa"]
    assert diffs[1].snapshot == {}


def test_adsb_filters_reach_the_query(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(url__startswith=ADSB_URL).mock(
        side_effect=responses(aircraft_payload(row("aaaaaa")))
    )

    list(
        poll.adsb(
            interval=10,
            max_iterations=1,
            sleep=waits,
            bbox=(51.0, -1.0, 52.0, 0.5),
            min_alt=1000,
            airline="British",
        )
    )

    params = route.calls.last.request.url.params
    assert params["bbox"] == "51.0,-1.0,52.0,0.5"
    assert params["min_alt"] == "1000"
    assert params["airline"] == "British"
    assert params["photos"] == "false"


def test_adsb_survives_a_rate_limit(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=ADSB_URL).mock(
        side_effect=[
            httpx.Response(200, json=aircraft_payload(row("aaaaaa"))),
            httpx.Response(429, headers={"Retry-After": "3"}, json={"detail": "quota"}),
            httpx.Response(200, json=aircraft_payload(row("aaaaaa"), row("bbbbbb"))),
        ]
    )

    diffs = list(poll.adsb(interval=10, max_iterations=3, sleep=waits))

    assert len(diffs) == 2  # the rate-limited poll yields nothing
    assert [a.icao24 for a in diffs[1].appeared] == ["bbbbbb"]
    assert waits.calls == [10, 3]


def test_adsb_propagates_a_bad_bounding_box(
    poll: Poll, waits: SleepRecorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=ADSB_URL).mock(
        return_value=httpx.Response(400, json={"detail": "Invalid bounding box"})
    )

    with pytest.raises(BadRequestError):
        list(poll.adsb(bbox=(52.0, 0.5, 51.0, -1.0), sleep=waits))


# ── argument validation (no I/O) ─────────────────────────────────────────────


@pytest.mark.parametrize("kwargs", [{"interval": -1.0}, {"max_iterations": 0}])
def test_pollers_reject_bad_loop_settings_before_any_request(
    poll: Poll, respx_mock: respx.MockRouter, kwargs: dict[str, Any]
) -> None:
    route = respx_mock.get(url__startswith=TEST_BASE_URL).mock(
        return_value=httpx.Response(200, json={})
    )

    with pytest.raises(ValueError):
        poll.flight_status("BA123", **kwargs)
    with pytest.raises(ValueError):
        poll.adsb(**kwargs)

    assert route.call_count == 0


# ── client wiring ────────────────────────────────────────────────────────────


def test_client_exposes_poll_as_a_cached_namespace(poll_client: SkyLink) -> None:
    assert isinstance(poll_client.poll, Poll)
    assert poll_client.poll is poll_client.poll
    assert poll_client.poll._client is poll_client


async def test_async_client_exposes_the_async_namespace(async_client: AsyncSkyLink) -> None:
    """A copy-paste slip in the wiring would hand back the blocking class."""

    assert type(async_client.poll) is AsyncPoll
    assert not isinstance(async_client.poll, Poll)


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_flight_status_mirrors_the_sync_poller(
    async_client: AsyncSkyLink, async_waits: AsyncSleepRecorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(STATUS_URL).mock(
        side_effect=responses(
            status_payload("En Route"),
            status_payload("En Route"),
            status_payload("Landed"),
        )
    )

    seen = [
        status
        async for status in AsyncPoll(async_client).flight_status(
            "BA123", interval=60, sleep=async_waits
        )
    ]

    assert [status.status for status in seen] == ["En Route", "Landed"]
    assert async_waits.calls == [60, 60]


async def test_async_adsb_diffs(
    async_client: AsyncSkyLink, async_waits: AsyncSleepRecorder, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(url__startswith=ADSB_URL).mock(
        side_effect=responses(
            aircraft_payload(row("aaaaaa")),
            aircraft_payload(row("aaaaaa", altitude=34000), row("bbbbbb")),
        )
    )

    diffs = [
        diff
        async for diff in AsyncPoll(async_client).adsb(
            interval=10, max_iterations=2, sleep=async_waits
        )
    ]

    assert diffs[0].is_first is True
    assert [a.icao24 for a in diffs[1].appeared] == ["bbbbbb"]
    assert [a.icao24 for a in diffs[1].updated] == ["aaaaaa"]
    assert async_waits.calls == [10]


async def test_async_poll_validates_eagerly(async_client: AsyncSkyLink) -> None:
    with pytest.raises(ValueError):
        AsyncPoll(async_client).flight_status("BA123", max_iterations=0)
    with pytest.raises(ValueError):
        AsyncPoll(async_client).adsb(interval=-5)
