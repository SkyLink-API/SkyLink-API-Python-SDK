"""GeoJSON (RFC 7946) exporters — feed SkyLink responses straight to a map.

The output is plain ``dict``/``list``, typed with :class:`~typing.TypedDict`
rather than pydantic: this is a serialisation format, not an API response, and it
has to stay ``json.dumps``-able and directly assignable to a Leaflet/Mapbox/
deck.gl source without a conversion step.

Two rules the whole module is built around:

* **Coordinates are ``[longitude, latitude]``.** Every other part of this SDK
  (and every aviation payload) puts latitude first, so this is the single most
  common GeoJSON bug. It is inverted exactly once, in :func:`_position`.
* **Nothing is fabricated.** Objects without usable coordinates are skipped and
  ``None`` properties are dropped, so a Mode S aircraft that never broadcast a
  position does not become a feature at (0, 0).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date, datetime
from typing import Any, Literal, TypedDict, TypeGuard

from .spatial import Point, _as_utc, point_coords
from .units import _attr

__all__ = [
    "Feature",
    "FeatureCollection",
    "Geometry",
    "adsb_to_geojson",
    "airports_to_geojson",
    "navaids_to_geojson",
    "track_to_geojson",
]


class Geometry(TypedDict):
    """A GeoJSON geometry. ``coordinates`` shape depends on ``type``:
    ``[lon, lat]`` for a ``Point``, ``[[lon, lat], ...]`` for a ``LineString``."""

    type: str
    coordinates: Any


class Feature(TypedDict):
    """A GeoJSON feature: one geometry plus its properties."""

    type: Literal["Feature"]
    geometry: Geometry
    properties: dict[str, Any]


class FeatureCollection(TypedDict):
    """A GeoJSON ``FeatureCollection`` — what map libraries accept as a source."""

    type: Literal["FeatureCollection"]
    features: list[Feature]


def _position(point: Point) -> list[float]:
    """``(latitude, longitude)`` → ``[longitude, latitude]``. The one flip."""

    latitude, longitude = point
    return [longitude, latitude]


def _clean(value: Any) -> Any:
    """Make a property value JSON-safe (datetimes → ISO-8601 strings)."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _properties(source: object, names: Iterable[str]) -> dict[str, Any]:
    """Collect ``names`` off ``source``, dropping the ones that are ``None``."""

    properties: dict[str, Any] = {}
    for name in names:
        value = _attr(source, name)
        if value is not None:
            properties[name] = _clean(value)
    return properties


def _feature(geometry: Geometry, properties: dict[str, Any]) -> Feature:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def _point_feature(point: Point, properties: dict[str, Any]) -> Feature:
    return _feature({"type": "Point", "coordinates": _position(point)}, properties)


def _collection(features: list[Feature]) -> FeatureCollection:
    return {"type": "FeatureCollection", "features": features}


def _is_item_list(value: object) -> TypeGuard[Iterable[object]]:
    """Whether ``value`` is a list of items rather than a single object.

    Deliberately narrower than ``isinstance(value, Iterable)``: a pydantic model
    *is* iterable (it yields its own ``(field, value)`` pairs), so an envelope
    would otherwise be mistaken for its own contents.
    """

    if isinstance(value, (str, bytes, Mapping)):
        return False
    return isinstance(value, (Sequence, Iterator, set, frozenset))


def _items(source: object, *names: str) -> Iterable[object]:
    """Accept either a list of items or the envelope that contains one."""

    nested = _attr(source, *names)
    if _is_item_list(nested):
        return nested
    if _is_item_list(source):
        return source
    return ()


_ADSB_PROPERTIES = (
    "icao24",
    "callsign",
    "registration",
    "aircraft_type",
    "airline",
    "altitude",
    "altitude_baro",
    "ground_speed",
    "track",
    "vertical_rate",
    "is_on_ground",
    "last_seen",
    "first_seen",
    "photo_url",
)

_POSITION_PROPERTIES = (
    "timestamp",
    "icao24",
    "callsign",
    "altitude_baro",
    "altitude",
    "ground_speed",
    "track",
    "vertical_rate",
    "is_on_ground",
)

_TRACK_PROPERTIES = (
    "flight_id",
    "icao24",
    "callsign",
    "registration",
    "aircraft_type_icao",
    "departure_airport_icao",
    "departure_airport_iata",
    "arrival_airport_icao",
    "arrival_airport_iata",
    "takeoff_time",
    "landing_time",
)

_AIRPORT_PROPERTIES = (
    "id",
    "ident",
    "icao_code",
    "iata_code",
    "name",
    "type",
    "municipality",
    "iso_country",
    "iso_region",
    "elevation_ft",
    "continent",
    "scheduled_service",
    "distance_km",
    "relevance_score",
)

_NAVAID_PROPERTIES = (
    "id",
    "ident",
    "name",
    "type",
    "frequency_khz",
    "usage_type",
    "power",
    "elevation_ft",
    "iso_country",
    "associated_airport",
    "magnetic_variation_deg",
)


def adsb_to_geojson(states: object) -> FeatureCollection:
    """Live ADS-B aircraft → one ``Point`` feature each.

    Accepts the ``adsb.aircraft()`` envelope or a bare list of states. Aircraft
    without a position (Mode S only, or just acquired) are **skipped** — their
    ``latitude``/``longitude`` are ``null`` and a feature at (0, 0) in the Gulf of
    Guinea is worse than no feature.

    Properties carry the identity and the flight state (``icao24``, ``callsign``,
    ``altitude`` in feet, ``ground_speed`` in knots, ``track`` in degrees);
    timestamps become ISO-8601 strings.
    """

    features: list[Feature] = []
    for state in _items(states, "aircraft", "states"):
        coords = point_coords(state)
        if coords is None:
            continue
        features.append(_point_feature(coords, _properties(state, _ADSB_PROPERTIES)))
    return _collection(features)


def track_to_geojson(track: object, *, include_points: bool = False) -> FeatureCollection:
    """A flight track → one ``LineString`` feature, optionally plus its points.

    Accepts a ``history.track()`` envelope (its flight identity becomes the
    line's properties) or a bare list of positions. Positions arrive
    **newest-first** from the API, so they are re-sorted chronologically when
    they carry timestamps — a LineString drawn backwards is still correct, but
    ``include_points`` output would be confusing.

    A track with fewer than two usable positions produces no line (a
    one-coordinate ``LineString`` is invalid GeoJSON); with
    ``include_points=True`` you still get the single point.
    """

    positions = list(_items(track, "positions"))
    ordered: list[tuple[object, Point]] = []
    for position in positions:
        coords = point_coords(position)
        if coords is not None:
            ordered.append((position, coords))

    times = [_as_utc(_attr(position, "timestamp", "time", "seen_at")) for position, _ in ordered]
    if ordered and all(moment is not None for moment in times):
        pairs = zip(times, ordered, strict=True)
        stamped = [(moment, item) for moment, item in pairs if moment is not None]
        stamped.sort(key=lambda pair: pair[0])
        ordered = [item for _, item in stamped]

    features: list[Feature] = []
    if len(ordered) >= 2:
        properties = _properties(track, _TRACK_PROPERTIES)
        properties["point_count"] = len(ordered)
        features.append(
            _feature(
                {"type": "LineString", "coordinates": [_position(c) for _, c in ordered]},
                properties,
            )
        )
    if include_points:
        features.extend(
            _point_feature(coords, _properties(position, _POSITION_PROPERTIES))
            for position, coords in ordered
        )
    return _collection(features)


def airports_to_geojson(airports: object) -> FeatureCollection:
    """Airports → one ``Point`` feature each.

    Accepts any of the search envelopes (``airports``/``ident`` keyed), a bare
    list, or a single enriched airport. Coordinates come from
    ``latitude_deg``/``longitude_deg``; rows without them are skipped.
    ``distance_km`` and ``relevance_score`` are carried through when present, so
    a nearby-search result keeps its ranking on the map.
    """

    single = point_coords(airports)
    if single is not None and _attr(airports, "ident", "icao_code", "iata_code") is not None:
        return _collection([_point_feature(single, _properties(airports, _AIRPORT_PROPERTIES))])

    features: list[Feature] = []
    for airport in _items(airports, "airports"):
        coords = point_coords(airport)
        if coords is None:
            continue
        features.append(_point_feature(coords, _properties(airport, _AIRPORT_PROPERTIES)))
    return _collection(features)


def navaids_to_geojson(navaids: object) -> FeatureCollection:
    """Navaids → one ``Point`` feature each.

    Accepts the ``navaids.list()`` envelope, the ``navaids[]`` block of an
    enriched airport, or a bare list. ``frequency_khz`` is passed through
    untouched — it is a string inside an airport payload and a number from
    ``GET /navaids``, and coercing it here would hide that.
    """

    features: list[Feature] = []
    for navaid in _items(navaids, "navaids"):
        coords = point_coords(navaid)
        if coords is None:
            continue
        features.append(_point_feature(coords, _properties(navaid, _NAVAID_PROPERTIES)))
    return _collection(features)
