"""Pure helpers at work: a bounding box, derived weather, and GeoJSON on disk.

Nothing in ``skylink_api.helpers`` touches the network — they are plain
functions over values you already hold. This script uses four of the modules
together: :mod:`spatial` to build the query box, :mod:`weather` to derive the
flight category the API does not send, :mod:`units` to turn the unit-less
numbers into something printable, and :mod:`geojson` to write layers any map
(Leaflet, Mapbox, QGIS, ``geojson.io``) can open.

Run it with a key in the environment::

    export RAPIDAPI_KEY=...msh...jsn...   # or SKYLINK_API_KEY with provider="direct"
    python examples/map_export.py

It writes ``traffic.geojson`` and ``airports.geojson`` into the current
directory.
"""

from __future__ import annotations

import json
from pathlib import Path

from skylink_api import (
    APIStatusError,
    AuthenticationError,
    RateLimitError,
    SkyLink,
)
from skylink_api.helpers.geojson import adsb_to_geojson, airports_to_geojson
from skylink_api.helpers.idents import split_flight_number
from skylink_api.helpers.spatial import bbox_around, haversine_nm
from skylink_api.helpers.units import kt_to_kmh, normalize_altimeter
from skylink_api.helpers.weather import ceiling_ft, flight_category, is_stale, wind_components

AIRPORT, LAT, LON = "EGLL", 51.4706, -0.4619
RADIUS_KM = 60.0

OUT_DIR = Path.cwd()


def write_traffic_layer(sky: SkyLink) -> None:
    """Live traffic around the field as a Point FeatureCollection."""

    # A box is cheaper for the backend than a radius and is what the feed
    # filters on natively. cos(latitude) is accounted for, so 60 km is 60 km.
    box = bbox_around(LAT, LON, radius_km=RADIUS_KM)
    print(f"bbox {box}")

    page = sky.adsb.aircraft(bbox=box, limit=200)
    layer = adsb_to_geojson(page)

    # Coordinates are [longitude, latitude] — GeoJSON order, the opposite of
    # every SkyLink payload. Aircraft with no position are simply skipped, so
    # the feature count can be lower than the row count.
    path = OUT_DIR / "traffic.geojson"
    path.write_text(json.dumps(layer), encoding="utf-8")
    print(f"  {len(layer['features'])} of {len(page.aircraft)} rows written to {path.name}")

    for aircraft in page.aircraft[:5]:
        if aircraft.latitude is None or aircraft.longitude is None:
            continue
        distance = haversine_nm(LAT, LON, aircraft.latitude, aircraft.longitude)
        speed_kmh = kt_to_kmh(aircraft.ground_speed)
        flight = split_flight_number(aircraft.callsign)
        operator = flight.airline if flight else "?"
        print(
            f"  {aircraft.callsign or aircraft.icao24:9} {operator:4} "
            f"{distance:5.1f} nm  {speed_kmh or 0:6.0f} km/h"
        )


def write_airport_layer(sky: SkyLink) -> None:
    """Every airfield inside the same radius, as a second layer."""

    nearby = sky.airports.nearby(lat=LAT, lon=LON, radius=RADIUS_KM, limit=50)
    layer = airports_to_geojson(nearby)

    path = OUT_DIR / "airports.geojson"
    path.write_text(json.dumps(layer), encoding="utf-8")
    print(f"\n  {len(layer['features'])} airports written to {path.name}")


def show_derived_conditions(sky: SkyLink) -> None:
    """Flight category, ceiling, altimeter unit and crosswind — all derived."""

    report = sky.weather.metar(AIRPORT, parsed=True)

    print(f"\n{AIRPORT} {report.raw}")
    # The API never sends a flight category for a METAR; this computes it from
    # visibility and the lowest BKN/OVC layer. Needs parsed=True.
    print(f"  category  {flight_category(report) or 'unknown'}")
    print(f"  ceiling   {ceiling_ft(report)} ft")
    # A METAR older than 90 minutes means the station or the scraper is down.
    print(f"  stale     {is_stale(report)}")

    parsed = report.parsed
    if parsed is None:
        return

    # The altimeter arrives WITHOUT a unit: 29.92 and 1013 are the same pressure.
    # normalize_altimeter guesses by magnitude and returns both, or None when the
    # value is genuinely ambiguous.
    altimeter = normalize_altimeter(parsed.altimeter)
    if altimeter is not None:
        print(f"  pressure  {altimeter.in_hg:.2f} inHg / {altimeter.hpa:.0f} hPa")

    wind = parsed.wind
    if wind is not None:
        # 27L at Heathrow: 269.7° true. Runway headings live on the enriched
        # airport (`sky.airports.search(icao=...).runways`), as strings.
        components = wind_components(269.7, wind.direction, wind.speed)
        if components is None:
            print("  wind      variable or unknown")
        else:
            side = "right" if components.from_right else "left"
            print(
                f"  27L       {components.headwind_kt:+.0f} kt head, "
                f"{components.crosswind_kt:.0f} kt cross from the {side}"
            )


def main() -> None:
    try:
        with SkyLink() as sky:  # RapidAPI channel, api_key falls back to $RAPIDAPI_KEY
            write_traffic_layer(sky)
            write_airport_layer(sky)
            show_derived_conditions(sky)
    except AuthenticationError as err:
        raise SystemExit(f"Auth failed: {err}") from err
    except RateLimitError as err:
        raise SystemExit(f"Quota exhausted, retry in {err.retry_after}s") from err
    except APIStatusError as err:
        raise SystemExit(f"API error {err.status_code}: {err.message}") from err


if __name__ == "__main__":
    main()
