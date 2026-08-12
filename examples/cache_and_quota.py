"""Spending less quota: the opt-in response cache, quota hooks and client clones.

Three things that only exist because the API is metered:

* ``SkyLink(cache=MemoryCache(...))`` — **off by default**, and inert until you
  give it TTLs. Only successful GETs are cached, keyed by operation, so a POST
  or an error is never replayed.
* ``on_rate_limit`` / ``on_quota_low`` — the quota headers of every response,
  pushed to a callback instead of polled off ``last_rate_limit``.
* ``from_env`` and ``with_options`` — an explicit "key comes from the
  environment" constructor, and a clone that shares this client's connections.

Run it with a key in the environment::

    export RAPIDAPI_KEY=...msh...jsn...   # or SKYLINK_API_KEY with provider="direct"
    python examples/cache_and_quota.py
"""

from __future__ import annotations

import time

from skylink_api import (
    APIStatusError,
    AuthenticationError,
    MemoryCache,
    RateLimitError,
    RateLimitInfo,
    SkyLink,
)

#: TTLs are per **operation name**, by exact match ("weather.metar"), by
#: namespace prefix ("geo.*") or by "*" for everything. Seconds; 0 means "do not
#: cache", and it is the default — a bare MemoryCache() changes nothing.
CACHE = MemoryCache(
    default_ttl=0,
    ttls={
        "weather.metar": 60,  # reissued hourly; a minute of staleness is free
        "weather.taf": 300,
        "airports.*": 3600,  # reference data, changes about never
        "geo.*": 86_400,
        # "adsb.aircraft" is deliberately absent: caching a live feed is a bug.
    },
)


def warn_on_low_quota(info: RateLimitInfo) -> None:
    """Edge-triggered: fires once on the crossing, re-arms on a new window."""

    print(f"  !! quota low: {info.remaining}/{info.limit} left, resets in {info.reset}s")


def show_cache(sky: SkyLink) -> None:
    """The same call twice: once over the network, once from memory."""

    started = time.perf_counter()
    first = sky.weather.metar("EGLL")
    network_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    second = sky.weather.metar("EGLL")
    cached_ms = (time.perf_counter() - started) * 1000

    print(f"METAR {first.icao}: {network_ms:.0f} ms, then {cached_ms:.0f} ms (cache)")
    # A hit re-validates the stored payload, so the second call is an equal but
    # separate object: mutating one cannot corrupt the next caller's copy.
    print(f"  same value: {first.raw == second.raw}, same object: {first is second}")

    # Not cached: no TTL rule matches adsb.*, and default_ttl is 0.
    sky.adsb.statistics()
    print("  adsb.statistics(): not cached (no rule, default_ttl=0)")


def show_quota(sky: SkyLink) -> None:
    """Quota headers as they arrive, rather than polled afterwards."""

    seen: list[RateLimitInfo] = []
    stop = sky.on_rate_limit(seen.append)

    sky.weather.metar("KJFK")
    sky.weather.metar("LFPG")

    stop()  # unsubscribe; calling it twice is harmless

    if not seen:
        # Staging instances behind neither gateway inject no quota headers at all.
        print("\nNo quota headers on these responses.")
        return
    latest = seen[-1]
    print(f"\nQuota after {len(seen)} responses: {latest.remaining}/{latest.limit} left")


def show_clone(sky: SkyLink) -> None:
    """A clone with different settings, over the same connection pool."""

    patient = sky.with_options(timeout=60.0, max_retries=0)
    archive = sky.with_options(history_plan="mega")

    print("\nClones share the transport; closing the original closes them all.")
    # `config` is the public read-only view of the resolved settings; the repr
    # masks the key.
    print(f"  patient: retries={patient.config.max_retries}, cache={patient.cache is not None}")
    print(f"  archive: history_plan={archive.config.history_plan}")
    # The cache is shared with the original unless cache= is passed explicitly;
    # registered hooks are copied as a snapshot, not linked.


def main() -> None:
    try:
        # from_env() is SkyLink() with no api_key argument to get wrong: the
        # credential can only come from $RAPIDAPI_KEY / $SKYLINK_API_KEY.
        with SkyLink.from_env(cache=CACHE) as sky:
            sky.on_quota_low(warn_on_low_quota, threshold=0.1)  # last 10%
            show_cache(sky)
            show_quota(sky)
            show_clone(sky)
    except AuthenticationError as err:
        raise SystemExit(f"Auth failed: {err}") from err
    except RateLimitError as err:
        raise SystemExit(f"Quota exhausted, retry in {err.retry_after}s") from err
    except APIStatusError as err:
        raise SystemExit(f"API error {err.status_code}: {err.message}") from err


if __name__ == "__main__":
    main()
