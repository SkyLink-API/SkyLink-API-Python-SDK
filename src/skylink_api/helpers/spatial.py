"""Great-circle geometry and bounding boxes — everything the API takes as a string.

``adsb.aircraft``, ``weather.pireps``, ``weather.winds_aloft`` and
``weather.airsigmet`` all want a bounding box as the single string
``"lat1,lon1,lat2,lon2"``. :func:`bbox` and :func:`bbox_around` build it (and
:func:`bbox_around` gets the longitude compression at high latitudes right,
which hand-rolled versions usually do not).

The distance functions exist so that "how far apart are these two points" does
not have to cost an API call: ``client.distance()`` stays the right tool when you
have *airport codes* and want the airport metadata back, but for two coordinates
you already hold, :func:`haversine_km` is free and instant.

Every function that consumes track points is duck-typed through
:func:`point_coords`: history positions, live ADS-B states, ``{"lat": …}`` dicts
and bare ``(lat, lon)`` pairs all work, and points without usable coordinates are
skipped rather than raising. Coordinates are always **(latitude, longitude)** in
this module; :mod:`skylink_api.helpers.geojson` is the one place that flips them.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from itertools import pairwise
from typing import Any, NamedTuple, TypeAlias, TypeVar

from .units import NAUTICAL_MILES_PER_KM, _attr, to_number

__all__ = [
    "EARTH_RADIUS_KM",
    "BBox",
    "Point",
    "TrackStats",
    "bbox",
    "bbox_around",
    "destination_point",
    "great_circle_points",
    "haversine_km",
    "haversine_nm",
    "initial_bearing",
    "parse_bbox",
    "point_coords",
    "simplify_track",
    "track_stats",
]

#: Mean Earth radius (IUGG), kilometres. Every formula here is spherical.
EARTH_RADIUS_KM = 6371.0088

#: Kilometres per degree of latitude on that sphere.
_KM_PER_DEGREE_LATITUDE = EARTH_RADIUS_KM * math.pi / 180.0

#: A ``(latitude, longitude)`` pair in degrees. Note the order — GeoJSON uses the
#: opposite one.
Point: TypeAlias = tuple[float, float]

#: Whatever a caller's track is made of — a pydantic position, an ADS-B state, a
#: dict or a bare pair. :func:`simplify_track` hands the same type back.
_PointT = TypeVar("_PointT")


class BBox(NamedTuple):
    """A bounding box, corners normalised (south ≤ north, west ≤ east)."""

    south: float
    west: float
    north: float
    east: float


class TrackStats(NamedTuple):
    """Summary of a position track (``history.track()``, ``history.positions()``)."""

    distance_km: float
    """Great-circle length of the path, kilometres."""

    distance_nm: float
    """The same length in nautical miles."""

    duration_seconds: float | None
    """Span between the earliest and latest timestamp, or ``None`` when the
    points carry no usable time."""

    max_altitude_ft: float | None
    min_altitude_ft: float | None
    average_ground_speed_kt: float | None
    """Mean of the **reported** ground speeds; ``None`` when no point has one.
    It is not derived from distance/duration, so a gap in the track does not
    silently deflate it."""

    point_count: int
    """Number of points that had usable coordinates (the rest were skipped)."""

    start: datetime | None
    """Earliest timestamp, as UTC. History positions arrive newest-first, so this
    is not necessarily the first element."""

    end: datetime | None
    """Latest timestamp, as UTC."""


# ── point access ─────────────────────────────────────────────────────────────


def point_coords(obj: object) -> Point | None:
    """Read ``(latitude, longitude)`` off anything that looks like a position.

    Tried in order: ``latitude``/``longitude`` (history positions, ADS-B states,
    PIREPs), ``latitude_deg``/``longitude_deg`` (airports and navaids, straight
    from the OurAirports CSVs), ``lat``/``lon``, ``lat``/``lng``, and finally
    ``obj[0]``/``obj[1]`` for a bare pair. Returns ``None`` when no pair could be
    read or the values are out of range — a Mode S aircraft without an ADS-B
    position has ``latitude=None`` and must not blow up a whole track.
    """

    if obj is None:
        return None

    latitude = to_number(_attr(obj, "latitude", "latitude_deg", "lat"))
    longitude = to_number(_attr(obj, "longitude", "longitude_deg", "lon", "lng"))

    if (latitude is None or longitude is None) and isinstance(obj, Sequence):
        if isinstance(obj, (str, bytes)) or len(obj) < 2:
            return None
        latitude = to_number(obj[0])
        longitude = to_number(obj[1])

    if latitude is None or longitude is None:
        return None
    if not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
        return None
    return (latitude, longitude)


def _point_altitude_ft(obj: object) -> float | None:
    """Barometric altitude in feet. ``altitude_baro`` is the history spelling."""

    return to_number(_attr(obj, "altitude", "altitude_baro", "altitude_ft", "alt"))


def _point_speed_kt(obj: object) -> float | None:
    return to_number(_attr(obj, "ground_speed", "speed", "velocity"))


def _point_time(obj: object) -> datetime | None:
    return _as_utc(_attr(obj, "timestamp", "time", "seen_at", "last_seen"))


def _as_utc(value: object) -> datetime | None:
    """Coerce a wire timestamp to an aware UTC datetime.

    ADS-B timestamps are naive (the backend serialises ``datetime.now()`` without
    an offset) while history timestamps carry ``+00:00``; mixing the two in a
    subtraction raises. Naive values are read as UTC, which is what they are.
    """

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ── bounding boxes ───────────────────────────────────────────────────────────


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _format_coordinate(value: float) -> str:
    # 6 decimals ≈ 11 cm; enough for any bbox and it keeps float noise
    # (40.68000000000001) out of the query string.
    return repr(round(value, 6))


def bbox(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """Build the wire bounding box ``"lat1,lon1,lat2,lon2"`` (south-west first).

    Corners may be given in any order — they are sorted, latitudes clamped to
    ±90 and longitudes to ±180, so a box drawn from a map's north-east corner
    still comes out valid. Units are degrees.
    """

    box = parse_bbox((lat1, lon1, lat2, lon2))
    return ",".join(
        _format_coordinate(value) for value in (box.south, box.west, box.north, box.east)
    )


def bbox_around(lat: float, lon: float, radius_km: float) -> str:
    """Bounding box of ``radius_km`` around a point, as the wire string.

    A degree of longitude shrinks with ``cos(latitude)``, so a naive square box
    is far too narrow near the poles and slightly too wide at the equator; this
    accounts for it. The box is a square in kilometres, therefore a rectangle in
    degrees. Latitudes clamp at ±90 and longitudes at ±180 (no date-line
    wrapping: the API cannot express a box whose west edge is east of its east
    edge).
    """

    if radius_km < 0:
        raise ValueError("radius_km must not be negative")

    delta_lat = radius_km / _KM_PER_DEGREE_LATITUDE
    cos_lat = math.cos(math.radians(_clamp(lat, -90.0, 90.0)))
    delta_lon = 180.0 if cos_lat <= 1e-9 else min(180.0, delta_lat / cos_lat)
    return bbox(lat - delta_lat, lon - delta_lon, lat + delta_lat, lon + delta_lon)


def parse_bbox(value: str | Iterable[float]) -> BBox:
    """Parse a bounding box string or 4-sequence into normalised corners.

    Accepts what the API accepts (``"40.5,-74.3,41.0,-73.5"``) plus any iterable
    of four numbers. Raises :class:`ValueError` when there are not exactly four
    readable numbers.
    """

    if isinstance(value, str):
        parts: list[Any] = value.split(",")
    else:
        parts = list(value)
    if len(parts) != 4:
        raise ValueError(f"bbox must have 4 values (lat1, lon1, lat2, lon2), got {len(parts)}")

    numbers: list[float] = []
    for part in parts:
        number = to_number(part)
        if number is None:
            raise ValueError(f"bbox value is not a number: {part!r}")
        numbers.append(number)

    lat1, lon1, lat2, lon2 = numbers
    lat1, lat2 = _clamp(lat1, -90.0, 90.0), _clamp(lat2, -90.0, 90.0)
    lon1, lon2 = _clamp(lon1, -180.0, 180.0), _clamp(lon2, -180.0, 180.0)
    return BBox(
        south=min(lat1, lat2),
        west=min(lon1, lon2),
        north=max(lat1, lat2),
        east=max(lon1, lon2),
    )


# ── great-circle maths ───────────────────────────────────────────────────────


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, kilometres.

    Sphere of radius :data:`EARTH_RADIUS_KM`; accurate to ~0.5 % against the
    ellipsoid, which is well inside what ADS-B positions justify.
    """

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, nautical miles."""

    return haversine_km(lat1, lon1, lat2, lon2) * NAUTICAL_MILES_PER_KM


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from the first point to the second, 0-360 degrees.

    True bearing, not magnetic. It changes along the route — this is the heading
    to depart on, not the one to hold.
    """

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination_point(lat: float, lon: float, bearing_deg: float, distance_km: float) -> Point:
    """Point reached by flying ``distance_km`` from ``(lat, lon)`` on ``bearing_deg``.

    Returns ``(latitude, longitude)`` in degrees, longitude normalised to ±180.
    """

    angular = distance_km / EARTH_RADIUS_KM
    phi1, lambda1 = math.radians(lat), math.radians(lon)
    theta = math.radians(bearing_deg)

    sin_phi2 = math.sin(phi1) * math.cos(angular) + math.cos(phi1) * math.sin(angular) * math.cos(
        theta
    )
    phi2 = math.asin(_clamp(sin_phi2, -1.0, 1.0))
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(angular) * math.cos(phi1),
        math.cos(angular) - math.sin(phi1) * math.sin(phi2),
    )
    longitude = (math.degrees(lambda2) + 540.0) % 360.0 - 180.0
    return (math.degrees(phi2), longitude)


def great_circle_points(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    *,
    count: int = 64,
) -> list[Point]:
    """Sample the great circle between two points, endpoints included.

    Use it to draw a route as a curve instead of the straight line a map library
    would otherwise render (which is wrong by hundreds of kilometres on a
    transatlantic leg). ``count`` is the total number of samples and must be
    at least 2. The result may cross the anti-meridian; split it yourself if your
    map library dislikes that.
    """

    if count < 2:
        raise ValueError("count must be at least 2")

    phi1, lambda1 = math.radians(lat1), math.radians(lon1)
    phi2, lambda2 = math.radians(lat2), math.radians(lon2)
    delta = haversine_km(lat1, lon1, lat2, lon2) / EARTH_RADIUS_KM
    if delta == 0.0:
        return [(lat1, lon1)] * count

    points: list[Point] = []
    sin_delta = math.sin(delta)
    for index in range(count):
        fraction = index / (count - 1)
        a = math.sin((1.0 - fraction) * delta) / sin_delta
        b = math.sin(fraction * delta) / sin_delta
        x = a * math.cos(phi1) * math.cos(lambda1) + b * math.cos(phi2) * math.cos(lambda2)
        y = a * math.cos(phi1) * math.sin(lambda1) + b * math.cos(phi2) * math.sin(lambda2)
        z = a * math.sin(phi1) + b * math.sin(phi2)
        latitude = math.degrees(math.atan2(z, math.hypot(x, y)))
        points.append((latitude, math.degrees(math.atan2(y, x))))
    return points


# ── tracks ───────────────────────────────────────────────────────────────────


def track_stats(points: Iterable[object]) -> TrackStats:
    """Summarise a position track: length, duration, altitude band, mean speed.

    Distances are great-circle sums over consecutive points **in the order
    given** (reversing a track does not change its length, so the newest-first
    ordering of ``history.track()`` is fine). Duration is taken from the earliest
    and latest timestamp found, which is order independent.

    Points without coordinates are skipped; an empty or coordinate-less input
    yields zero distances and ``None`` everywhere else rather than an error.
    """

    coordinates: list[Point] = []
    altitudes: list[float] = []
    speeds: list[float] = []
    times: list[datetime] = []

    for item in points:
        coords = point_coords(item)
        if coords is None:
            continue
        coordinates.append(coords)
        altitude = _point_altitude_ft(item)
        if altitude is not None:
            altitudes.append(altitude)
        speed = _point_speed_kt(item)
        if speed is not None:
            speeds.append(speed)
        moment = _point_time(item)
        if moment is not None:
            times.append(moment)

    distance_km = 0.0
    for (lat1, lon1), (lat2, lon2) in pairwise(coordinates):
        distance_km += haversine_km(lat1, lon1, lat2, lon2)

    start = min(times) if times else None
    end = max(times) if times else None
    duration = (end - start).total_seconds() if start is not None and end is not None else None

    return TrackStats(
        distance_km=distance_km,
        distance_nm=distance_km * NAUTICAL_MILES_PER_KM,
        duration_seconds=duration,
        max_altitude_ft=max(altitudes) if altitudes else None,
        min_altitude_ft=min(altitudes) if altitudes else None,
        average_ground_speed_kt=(sum(speeds) / len(speeds)) if speeds else None,
        point_count=len(coordinates),
        start=start,
        end=end,
    )


def _perpendicular_distance_km(point: Point, start: Point, end: Point) -> float:
    """Distance from ``point`` to the ``start``-``end`` segment, kilometres.

    Equirectangular projection around the segment: over the few hundred
    kilometres a Douglas-Peucker segment spans, the error is far below any
    sensible tolerance.
    """

    mean_lat = math.radians((start[0] + end[0] + point[0]) / 3.0)
    scale = math.cos(mean_lat)

    def project(p: Point) -> tuple[float, float]:
        return (p[1] * scale * _KM_PER_DEGREE_LATITUDE, p[0] * _KM_PER_DEGREE_LATITUDE)

    px, py = project(point)
    ax, ay = project(start)
    bx, by = project(end)

    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = _clamp(t, 0.0, 1.0)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def simplify_track(points: Sequence[_PointT], *, tolerance_km: float = 0.5) -> list[_PointT]:
    """Douglas-Peucker simplification of a track, keeping the **original items**.

    A long-haul track from ``history.track()`` is tens of thousands of 5-second
    samples — far more than any map can usefully draw. This drops every point
    that lies within ``tolerance_km`` of the line its neighbours already
    describe, typically shrinking a track by one to two orders of magnitude with
    no visible change.

    What comes back are the very objects that went in
    (:class:`~skylink_api.models.history.HistoryPosition`, ADS-B states, dicts,
    ``(lat, lon)`` pairs — whatever you passed), **not** coordinate pairs: the
    altitude, speed and timestamp on each point are exactly what a track is
    usually rendered or tooltipped with, and rebuilding them after a
    coordinates-only simplification is not possible.

    Input order is preserved and points without coordinates are skipped. Set
    ``tolerance_km=0`` to keep every point that is not exactly on the line.
    """

    if tolerance_km < 0:
        raise ValueError("tolerance_km must not be negative")

    kept_items: list[_PointT] = []
    coordinates: list[Point] = []
    for item in points:
        coords = point_coords(item)
        if coords is None:
            continue
        kept_items.append(item)
        coordinates.append(coords)
    if len(coordinates) <= 2:
        return kept_items

    keep = [False] * len(coordinates)
    keep[0] = keep[-1] = True
    stack: list[tuple[int, int]] = [(0, len(coordinates) - 1)]

    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        worst_index = -1
        worst_distance = -1.0
        for index in range(first + 1, last):
            distance = _perpendicular_distance_km(
                coordinates[index], coordinates[first], coordinates[last]
            )
            if distance > worst_distance:
                worst_distance = distance
                worst_index = index
        if worst_distance > tolerance_km:
            keep[worst_index] = True
            stack.append((first, worst_index))
            stack.append((worst_index, last))

    return [item for item, kept in zip(kept_items, keep, strict=True) if kept]
