"""Live ADS-B: aircraft in a radius, feed statistics, health and quota headers.

Run it with a key in the environment::

    export RAPIDAPI_KEY=...msh...jsn...   # or SKYLINK_API_KEY with provider="direct"
    python examples/adsb_tracking.py
"""

from __future__ import annotations

from skylink_api import (
    APIStatusError,
    AuthenticationError,
    RateLimitError,
    SkyLink,
)

# London Heathrow, 75 km around the field.
LAT, LON, RADIUS_KM = 51.4706, -0.4619, 75.0


def show_traffic(sky: SkyLink) -> None:
    """Aircraft currently in the box, newest position first."""

    result = sky.adsb.aircraft(
        lat=LAT,
        lon=LON,
        radius=RADIUS_KM,
        min_alt=1000,
        limit=25,
        photos=False,  # slow, and only covers the first 50 rows of a page
    )

    # total_count is the match count BEFORE paging — use it, not len(aircraft),
    # to decide whether another page exists.
    shown = len(result.aircraft)
    print(f"{result.total_count} aircraft within {RADIUS_KM:.0f} km, showing {shown}")

    for plane in result.aircraft:
        # A Mode S transponder that broadcasts only identity yields a row with no
        # position at all; that is normal, not an error.
        if plane.latitude is None or plane.longitude is None:
            print(f"  {plane.icao24}  (no position)")
            continue
        label = plane.callsign or plane.registration or plane.icao24
        print(
            f"  {label}  {plane.latitude:.3f},{plane.longitude:.3f}  "
            f"{plane.altitude} ft  {plane.ground_speed} kt  {plane.aircraft_type or '?'}"
        )

    if result.total_count > len(result.aircraft):
        page_two = sky.adsb.aircraft(lat=LAT, lon=LON, radius=RADIUS_KM, limit=25, offset=25)
        print(f"  ... next page has {len(page_two.aircraft)} more")


def show_quota(sky: SkyLink) -> None:
    """Quota snapshot from the last successful response.

    ``last_rate_limit`` is populated from whichever quota headers the channel's
    gateway injects — ``X-RateLimit-Requests-*`` on RapidAPI, ``X-RateLimit-*``
    on direct — so it stays ``None`` only against a staging instance that goes
    through neither.
    """

    quota = sky.last_rate_limit
    if quota is None:
        print("\nNo quota headers on the last response.")
        return
    print(f"\nQuota: {quota.remaining}/{quota.limit} left, resets in {quota.reset}s")


def show_feed_health(sky: SkyLink) -> None:
    """Feed-wide counters and ingest health."""

    stats = sky.adsb.statistics()
    print(
        f"\nFeed: {stats.total_aircraft} tracked "
        f"({stats.airborne} airborne, {stats.on_ground} on ground, "
        f"{stats.positioned_aircraft} with a position)"
    )
    altitudes = stats.altitude_stats
    if altitudes.max_altitude is not None:
        print(f"  altitudes {altitudes.min_altitude}-{altitudes.max_altitude} ft")

    # An unhealthy feed still answers 200 — this endpoint reports trouble, it
    # does not raise it.
    health = sky.adsb.health()
    print(f"  status {health.status} (connected={health.connected})")
    print(f"  last message {health.last_message_received}")


def main() -> None:
    try:
        with SkyLink() as sky:  # RapidAPI channel, api_key falls back to $RAPIDAPI_KEY
            show_traffic(sky)
            show_quota(sky)
            show_feed_health(sky)
    except AuthenticationError as err:
        raise SystemExit(f"Auth failed: {err}") from err
    except RateLimitError as err:
        quota = err.rate_limit
        left = quota.remaining if quota is not None else "?"
        raise SystemExit(f"Rate limited ({left} left), retry in {err.retry_after}s") from err
    except APIStatusError as err:
        raise SystemExit(f"API error {err.status_code}: {err.message}") from err


if __name__ == "__main__":
    main()
