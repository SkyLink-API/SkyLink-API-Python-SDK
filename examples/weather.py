"""Aviation weather: METAR (raw and decoded), TAF, and winds aloft.

Run it with a key in the environment::

    export RAPIDAPI_KEY=...msh...jsn...   # or SKYLINK_API_KEY with provider="direct"
    python examples/weather.py
"""

from __future__ import annotations

from skylink_api import (
    APIStatusError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
    SkyLink,
)

AIRPORT = "KJFK"

#: (lat1, lon1, lat2, lon2), south-west corner first. Winds aloft is a US-only
#: product, so a box over Europe legitimately answers 404.
CALIFORNIA = (34.0, -122.0, 40.0, -117.0)


def show_metar(sky: SkyLink) -> None:
    """The same observation twice: as filed, then decoded."""

    raw = sky.weather.metar(AIRPORT)
    print(f"METAR {raw.icao} ({raw.airport_name})")
    print(f"  {raw.raw}")

    decoded = sky.weather.metar(AIRPORT, parsed=True)
    # `parsed` is nullable even with parsed=True: the API sends null instead of
    # failing when the decoder chokes on an unusual report.
    if decoded.parsed is None:
        print("  (no decoded form available)")
        return

    fields = decoded.parsed
    wind = fields.wind
    if wind is not None:
        gust = f" gusting {wind.gust}kt" if wind.gust else ""
        print(f"  wind      {wind.direction}° at {wind.speed}kt{gust}")
    if fields.visibility is not None:
        print(f"  visibility {fields.visibility.repr or fields.visibility.value}")
    for layer in fields.clouds:
        print(f"  clouds    {layer.type} at {layer.base}00 ft")
    print(f"  temp/dew  {fields.temperature}/{fields.dewpoint} °C")
    print(f"  rules     {fields.flight_rules}")


def show_taf(sky: SkyLink) -> None:
    """Forecast for the same field, with its decoded periods."""

    taf = sky.weather.taf(AIRPORT, parsed=True)
    print(f"\nTAF {taf.icao}")
    print(f"  {taf.raw}")

    if taf.parsed is None:
        return
    for period in taf.parsed.forecast:
        print(f"  {period.type or 'BASE':6} {period.start_time} → {period.end_time}", end="")
        print(f"  {period.flight_rules or ''}")


def show_winds_aloft(sky: SkyLink) -> None:
    """Forecast winds and temperatures in a bounding box."""

    try:
        winds = sky.weather.winds_aloft(bbox=CALIFORNIA, forecast=12, level="low")
    except NotFoundError:
        print("\nNo FB winds station inside the box (US coverage only).")
        return

    print(f"\nWinds aloft — {winds.total} stations, valid {winds.valid_time}")
    for station in winds.stations[:3]:
        print(f"  {station.station}")
        for level in station.winds:
            # Every numeric field is optional — a station can report an altitude
            # with no usable wind, so never format these without a guard.
            if level.light_and_variable:
                print(f"    {level.altitude_ft} ft  light and variable")
            else:
                print(
                    f"    {level.altitude_ft} ft  "
                    f"{level.wind_direction}° / {level.wind_speed_kt}kt  "
                    f"{level.temperature_c}°C"
                )


def main() -> None:
    # RapidAPI channel by default; api_key falls back to $RAPIDAPI_KEY (and to
    # $SKYLINK_API_KEY). Pass provider="direct" for data.skylinkapi.com instead.
    try:
        with SkyLink() as sky:
            show_metar(sky)
            show_taf(sky)
            show_winds_aloft(sky)
    except AuthenticationError as err:
        raise SystemExit(f"Auth failed: {err}") from err
    except RateLimitError as err:
        # .retry_after is the server's Retry-After in seconds (None when absent);
        # .rate_limit carries the quota snapshot from the response headers.
        raise SystemExit(f"Quota exhausted, retry in {err.retry_after}s") from err
    except APIStatusError as err:
        raise SystemExit(f"API error {err.status_code}: {err.message}") from err


if __name__ == "__main__":
    main()
