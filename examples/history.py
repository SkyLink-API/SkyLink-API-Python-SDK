"""Historical ADS-B: archived flights, the flown track, and raw positions.

The data plan is part of the URL (``/ultra/history/...`` vs ``/mega/history/...``),
not a query parameter. Resolution order is: the per-call ``plan`` argument, then
the client's ``history_plan``, then ``"ultra"``.

Run it with a key in the environment::

    export RAPIDAPI_KEY=...msh...jsn...   # or SKYLINK_API_KEY with provider="direct"
    python examples/history.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from skylink_api import (
    APIStatusError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
    SkyLink,
)

REGISTRATION = "G-STBA"
ICAO24 = "4ca1fb"


def recent_flights(sky: SkyLink) -> str | None:
    """Archived flights for one airframe; returns a flight id to drill into."""

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)

    result = sky.history.flights(registration=REGISTRATION, start=start, end=end, limit=20)

    # An empty window is a normal 200 carrying a `note`, not a 404.
    if result.count == 0:
        print(f"No flights for {REGISTRATION}: {result.note}")
        return None

    print(f"{result.count} flights for {REGISTRATION}")
    for flight in result.flights:
        departure = flight.departure_airport_icao or "????"
        arrival = flight.arrival_airport_icao or "????"
        route = f"{departure} → {arrival}"
        print(
            f"  {flight.flight_id}  {flight.callsign or '-':8} {route}  "
            f"{flight.takeoff_time}  {flight.flight_duration_min} min"
        )

    return result.flights[0].flight_id


def show_track(sky: SkyLink, flight_id: str) -> None:
    """The flown path of a single flight."""

    track = sky.history.track(flight_id, limit=500)
    print(f"\nTrack {track.flight_id}: {track.count} points")
    for point in track.positions[:5]:
        print(
            f"  {point.timestamp}  {point.latitude},{point.longitude}  "
            f"{point.altitude_baro} ft  {point.ground_speed} kt"
        )


def show_positions(sky: SkyLink) -> None:
    """Two ways to ask for positions, and the dispatching shortcut."""

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=12)

    # Explicit: no guessing about what the identifier means.
    by_address = sky.history.positions_by_icao24(ICAO24, start=start, end=end, limit=1000)
    by_tail = sky.history.positions_by_registration(REGISTRATION, start=start, end=end, limit=1000)

    # Shortcut: six hex characters are read as an ICAO24 address, anything else
    # as a registration. "ABC123" is both — the address wins, so use the explicit
    # methods when the identifier type is known.
    dispatched = sky.history.positions(REGISTRATION, start=start, end=end)

    print(f"\npositions_by_icao24({ICAO24!r})        → {by_address.count} points")
    print(f"positions_by_registration({REGISTRATION!r}) → {by_tail.count} points")
    print(f"positions({REGISTRATION!r})              → {dispatched.count} points")


def show_mega_window(sky: SkyLink) -> None:
    """The mega plan reaches 365 days back instead of 90 — and has its own path."""

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=180)

    try:
        result = sky.history.flights(
            departure_icao="EGLL",
            arrival_icao="KJFK",
            start=start,
            end=end,
            limit=5,
            plan="mega",  # → GET /mega/history/flights
        )
    except PermissionDeniedError:
        # Calling a plan the key is not subscribed to is a 403 from the gateway,
        # not a client-side error.
        print("\nThis key is not subscribed to the mega plan.")
        return

    print(f"\nmega: {result.count} EGLL → KJFK flights in the last 180 days")

    # The same choice can be made once, for the whole client:
    #     SkyLink(history_plan="mega")


def main() -> None:
    try:
        with SkyLink() as sky:  # RapidAPI channel, api_key falls back to $RAPIDAPI_KEY
            flight_id = recent_flights(sky)
            if flight_id is not None:
                show_track(sky, flight_id)
            show_positions(sky)
            show_mega_window(sky)
    except AuthenticationError as err:
        raise SystemExit(f"Auth failed: {err}") from err
    except RateLimitError as err:
        raise SystemExit(f"Quota exhausted, retry in {err.retry_after}s") from err
    except APIStatusError as err:
        raise SystemExit(f"API error {err.status_code}: {err.message}") from err


if __name__ == "__main__":
    main()
