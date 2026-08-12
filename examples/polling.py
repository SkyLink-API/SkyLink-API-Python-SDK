"""Watching things change: ``sky.poll`` (and the paging iterators).

The API has neither streaming nor long polling, so "follow this flight" and
"keep this map fresh" are both loops. ``sky.poll`` is that loop written once:
the first request goes out immediately, ``interval`` is the pause *between*
requests, 429/5xx are survived (with ``Retry-After`` honoured) and 401/403/422
are not — a wrong key never fixes itself.

Both pollers are generators: nothing happens until you iterate, and ``break``
stops them at once. This example caps everything with ``max_iterations`` so it
terminates on its own.

Run it with a key in the environment::

    export RAPIDAPI_KEY=...msh...jsn...   # or SKYLINK_API_KEY with provider="direct"
    python examples/polling.py
"""

from __future__ import annotations

from skylink_api import (
    APIStatusError,
    AuthenticationError,
    RateLimitError,
    SkyLink,
)
from skylink_api.helpers.spatial import bbox_around

#: Around London Heathrow. Poll a *filtered* feed: unfiltered it is 9-10k rows
#: every tick.
BOX = bbox_around(51.4706, -0.4619, radius_km=40)

FLIGHT = "BA117"

#: Short and bounded so the example finishes in under a minute. In production
#: use ~10 s for ADS-B (the feed's own refresh) and ~60 s for a flight status
#: (the scraped page does not update faster).
ADSB_INTERVAL, ADSB_TICKS = 5.0, 3
STATUS_INTERVAL, STATUS_TICKS = 15.0, 2


def follow_the_map(sky: SkyLink) -> None:
    """ADS-B as diffs: what to add, what to remove, what to move."""

    print(f"Watching {BOX} for {ADSB_TICKS} ticks of {ADSB_INTERVAL:.0f}s")

    for tick, diff in enumerate(
        sky.poll.adsb(bbox=BOX, interval=ADSB_INTERVAL, max_iterations=ADSB_TICKS), start=1
    ):
        if diff.is_first:
            # The first tick reports the whole feed as `appeared` — draw it all.
            print(f"  tick {tick}: {len(diff.snapshot)} aircraft in the box")
            continue

        print(
            f"  tick {tick}: +{len(diff.appeared)} -{len(diff.disappeared)} "
            f"~{len(diff.updated)} (now {len(diff.snapshot)})"
        )
        for aircraft in diff.appeared[:3]:
            print(f"    entered: {aircraft.callsign or aircraft.icao24} at {aircraft.altitude} ft")
        for address in diff.disappeared[:3]:
            print(f"    left:    {address}")

    # An empty diff is information too: the feed is alive and nothing moved.
    # Only position, altitude and ground speed count as "updated" — last_seen
    # advances on every message and would put the whole feed in `updated`.


def follow_the_flight(sky: SkyLink) -> None:
    """A flight status until it lands — or until the tick budget runs out."""

    print(f"\nWatching {FLIGHT}")

    for status in sky.poll.flight_status(
        FLIGHT,
        interval=STATUS_INTERVAL,
        max_iterations=STATUS_TICKS,  # an unknown number stays "Unknown" forever
        changes_only=True,  # yields only when something meaningful changed
        until_terminal=True,  # stops itself on landed/arrived/cancelled/diverted
    ):
        gate = status.arrival.gate if status.arrival else None
        print(f"  {status.status or '?'} — gate {gate or '--'}")

    print("  (stopped: landed, or out of ticks)")


def walk_every_page(sky: SkyLink) -> None:
    """``iter_aircraft`` pages the one paginated endpoint for you."""

    print("\nPaging the feed")
    seen = 0
    for aircraft in sky.adsb.iter_aircraft(bbox=BOX, page_size=50, max_items=120):
        seen += 1
        if seen <= 3:
            print(f"  {aircraft.icao24} {aircraft.callsign or ''}")
    print(f"  walked {seen} rows (stops on a short or empty page)")


def main() -> None:
    try:
        with SkyLink() as sky:  # RapidAPI channel, api_key falls back to $RAPIDAPI_KEY
            follow_the_map(sky)
            follow_the_flight(sky)
            walk_every_page(sky)
    except KeyboardInterrupt:
        print("\nStopped.")
    except AuthenticationError as err:
        # Pollers never retry this: a bad key would burn quota forever.
        raise SystemExit(f"Auth failed: {err}") from err
    except RateLimitError as err:
        # Reached only when the quota is gone for good — inside the loop a 429
        # is waited out, not raised.
        raise SystemExit(f"Quota exhausted, retry in {err.retry_after}s") from err
    except APIStatusError as err:
        raise SystemExit(f"API error {err.status_code}: {err.message}") from err


if __name__ == "__main__":
    main()
