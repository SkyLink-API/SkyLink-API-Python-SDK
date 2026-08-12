"""AsyncSkyLink: the same surface, awaited — and fanned out with asyncio.gather.

One client owns one connection pool, so the right pattern is to build it once
and share it across concurrent tasks rather than per request.

Run it with a key in the environment::

    export SKYLINK_API_KEY=sk_live_...
    python examples/async_usage.py
"""

from __future__ import annotations

import asyncio

from skylink_api import (
    APIStatusError,
    AsyncSkyLink,
    AuthenticationError,
    RateLimitError,
)

AIRPORTS = ["KJFK", "EGLL", "LFPG", "EDDF", "RJTT"]


async def board_snapshot(sky: AsyncSkyLink) -> None:
    """Five unrelated calls, issued in parallel over the shared pool."""

    metars, status, live, distance = await asyncio.gather(
        asyncio.gather(*(sky.weather.metar(icao) for icao in AIRPORTS)),
        sky.flight_status("BA117"),
        sky.adsb.aircraft(lat=51.47, lon=-0.46, radius=50, limit=10),
        sky.distance(from_icao="KJFK", to_icao="EGLL", unit="nm"),
    )

    print("METARs")
    for metar in metars:
        print(f"  {metar.icao}: {metar.raw}")

    # flight_status is scraped: every field is an optional string and empty
    # values arrive as "" or "--". Times are opaque strings, never datetimes.
    print(f"\n{status.flight_number} ({status.airline}) — {status.status}")
    if status.departure is not None:
        print(f"  dep {status.departure.airport} {status.departure.scheduled_time}")
    if status.arrival is not None:
        print(f"  arr {status.arrival.airport} gate {status.arrival.gate}")

    print(f"\n{live.total_count} aircraft near LHR")
    print(f"KJFK → EGLL: {distance.distance} {distance.unit}, bearing {distance.bearing_cardinal}")


async def resilient_fanout(sky: AsyncSkyLink) -> None:
    """``return_exceptions=True`` keeps one failing leg from cancelling the rest."""

    results = await asyncio.gather(
        sky.weather.taf("KJFK"),
        sky.weather.taf("ZZZZ"),  # unknown airport → NotFoundError
        return_exceptions=True,
    )

    print()
    for icao, result in zip(["KJFK", "ZZZZ"], results, strict=True):
        if isinstance(result, APIStatusError):
            print(f"  {icao}: failed with {type(result).__name__} ({result.status_code})")
        elif isinstance(result, BaseException):
            raise result
        else:
            print(f"  {icao}: {result.raw}")


async def main() -> None:
    try:
        # api_key falls back to $SKYLINK_API_KEY; `async with` closes the pool.
        async with AsyncSkyLink() as sky:
            await board_snapshot(sky)
            await resilient_fanout(sky)
    except AuthenticationError as err:
        raise SystemExit(f"Auth failed: {err}") from err
    except RateLimitError as err:
        # Backoff between retries is a real `await asyncio.sleep`, so the event
        # loop keeps running while a throttled call waits.
        raise SystemExit(f"Quota exhausted, retry in {err.retry_after}s") from err
    except APIStatusError as err:
        raise SystemExit(f"API error {err.status_code}: {err.message}") from err


if __name__ == "__main__":
    asyncio.run(main())
