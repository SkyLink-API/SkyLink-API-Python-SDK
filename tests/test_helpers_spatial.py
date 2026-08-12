"""Bounding boxes and great-circle geometry (helpers §2).

The track functions are exercised against both pydantic models
(``HistoryPosition``, ``AdsbAircraft``) and bare dicts/tuples, because callers
hold all three.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from skylink_api.helpers.spatial import (
    EARTH_RADIUS_KM,
    bbox,
    bbox_around,
    destination_point,
    great_circle_points,
    haversine_km,
    haversine_nm,
    initial_bearing,
    parse_bbox,
    point_coords,
    simplify_track,
    track_stats,
)
from skylink_api.models.adsb import AdsbAircraft
from skylink_api.models.airports import Airport
from skylink_api.models.history import HistoryPosition

JFK = (40.639751, -73.778925)
LHR = (51.4706, -0.461941)
#: Published great-circle distance JFK → LHR, nautical miles.
JFK_LHR_NM = 2991.0


# ── bbox ─────────────────────────────────────────────────────────────────────


def test_bbox_emits_the_wire_string() -> None:
    assert bbox(40.5, -74.3, 41.0, -73.5) == "40.5,-74.3,41.0,-73.5"


def test_bbox_normalises_corner_order() -> None:
    """A box dragged from the north-east corner is still a valid box."""

    assert bbox(41.0, -73.5, 40.5, -74.3) == bbox(40.5, -74.3, 41.0, -73.5)


def test_bbox_clamps_out_of_range_corners() -> None:
    assert bbox(-95.0, -200.0, 95.0, 200.0) == "-90.0,-180.0,90.0,180.0"


def test_bbox_around_accounts_for_longitude_compression() -> None:
    """A degree of longitude is half a degree of latitude at 60° N."""

    box = parse_bbox(bbox_around(60.0, 10.0, 100.0))
    lat_span = box.north - box.south
    lon_span = box.east - box.west
    assert lon_span == pytest.approx(lat_span * 2.0, rel=1e-3)


def test_bbox_around_covers_the_requested_radius() -> None:
    box = parse_bbox(bbox_around(*JFK, 50.0))
    assert haversine_km(JFK[0], JFK[1], box.north, JFK[1]) == pytest.approx(50.0, rel=1e-3)
    assert haversine_km(JFK[0], JFK[1], JFK[0], box.east) == pytest.approx(50.0, rel=1e-3)


def test_bbox_around_clamps_at_the_pole() -> None:
    box = parse_bbox(bbox_around(89.5, 0.0, 500.0))
    assert box.north == 90.0
    assert box.west == -180.0 and box.east == 180.0


def test_bbox_around_rejects_a_negative_radius() -> None:
    with pytest.raises(ValueError, match="radius_km"):
        bbox_around(40.0, -73.0, -1.0)


def test_parse_bbox_accepts_the_string_and_a_sequence() -> None:
    from_string = parse_bbox("41.0,-73.5,40.5,-74.3")
    from_sequence = parse_bbox([41.0, -73.5, 40.5, -74.3])
    assert from_string == from_sequence
    assert from_string.south == 40.5
    assert from_string.west == -74.3
    assert from_string.north == 41.0
    assert from_string.east == -73.5


@pytest.mark.parametrize("value", ["1,2,3", "1,2,3,4,5", "a,b,c,d", ""])
def test_parse_bbox_rejects_malformed_input(value: str) -> None:
    with pytest.raises(ValueError):
        parse_bbox(value)


# ── great-circle ─────────────────────────────────────────────────────────────


def test_haversine_matches_the_published_jfk_lhr_distance() -> None:
    assert haversine_nm(*JFK, *LHR) == pytest.approx(JFK_LHR_NM, rel=2e-3)
    assert haversine_km(*JFK, *LHR) == pytest.approx(JFK_LHR_NM * 1.852, rel=2e-3)


def test_haversine_is_zero_for_identical_points() -> None:
    assert haversine_km(*JFK, *JFK) == 0.0


def test_haversine_handles_antipodes() -> None:
    assert haversine_km(0.0, 0.0, 0.0, 180.0) == pytest.approx(
        EARTH_RADIUS_KM * 3.14159265, rel=1e-6
    )


def test_initial_bearing_is_in_the_zero_to_360_range() -> None:
    assert initial_bearing(*JFK, *LHR) == pytest.approx(51.3, abs=0.5)
    assert initial_bearing(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0)
    assert initial_bearing(0.0, 0.0, 0.0, -1.0) == pytest.approx(270.0)


def test_destination_point_is_the_inverse_of_haversine_and_bearing() -> None:
    bearing = initial_bearing(*JFK, *LHR)
    distance = haversine_km(*JFK, *LHR)
    latitude, longitude = destination_point(*JFK, bearing, distance)
    assert latitude == pytest.approx(LHR[0], abs=1e-6)
    assert longitude == pytest.approx(LHR[1], abs=1e-6)


def test_great_circle_points_includes_both_endpoints() -> None:
    points = great_circle_points(*JFK, *LHR, count=10)
    assert len(points) == 10
    assert points[0] == pytest.approx(JFK)
    assert points[-1] == pytest.approx(LHR)


def test_great_circle_points_bulge_north_of_the_straight_line() -> None:
    """The whole reason to draw an arc: the mid-point is not the mean latitude."""

    points = great_circle_points(*JFK, *LHR, count=3)
    assert points[1][0] > (JFK[0] + LHR[0]) / 2


def test_great_circle_points_rejects_a_degenerate_count() -> None:
    with pytest.raises(ValueError, match="count"):
        great_circle_points(*JFK, *LHR, count=1)


# ── point_coords ─────────────────────────────────────────────────────────────


def test_point_coords_reads_every_shape_the_sdk_produces() -> None:
    assert point_coords(HistoryPosition(latitude=40.0, longitude=-73.0)) == (40.0, -73.0)
    assert point_coords(AdsbAircraft(latitude=40.0, longitude=-73.0)) == (40.0, -73.0)
    assert point_coords(Airport(latitude_deg=40.0, longitude_deg=-73.0)) == (40.0, -73.0)
    assert point_coords({"lat": 40.0, "lon": -73.0}) == (40.0, -73.0)
    assert point_coords({"lat": 40.0, "lng": -73.0}) == (40.0, -73.0)
    assert point_coords((40.0, -73.0)) == (40.0, -73.0)
    assert point_coords(["40.0", "-73.0"]) == (40.0, -73.0)


def test_point_coords_returns_none_for_a_positionless_aircraft() -> None:
    """Mode S without ADS-B: identity only, no position."""

    assert point_coords(AdsbAircraft(icao24="4ca1d3")) is None
    assert point_coords({"latitude": None, "longitude": -73.0}) is None
    assert point_coords(None) is None
    assert point_coords({"latitude": 91.0, "longitude": 0.0}) is None


# ── track stats ──────────────────────────────────────────────────────────────


def _position(
    lat: float, lon: float, minute: int, *, altitude: float, speed: float
) -> HistoryPosition:
    return HistoryPosition(
        latitude=lat,
        longitude=lon,
        altitude_baro=altitude,
        ground_speed=speed,
        timestamp=datetime(2026, 8, 12, 10, minute, tzinfo=timezone.utc),
    )


NEWEST_FIRST = [
    _position(41.0, -73.0, 20, altitude=30000, speed=460),
    _position(40.8, -73.4, 10, altitude=20000, speed=420),
    _position(40.6, -73.8, 0, altitude=1000, speed=180),
]


def test_track_stats_summarises_a_history_track() -> None:
    stats = track_stats(NEWEST_FIRST)
    assert stats.point_count == 3
    assert stats.distance_km == pytest.approx(
        haversine_km(41.0, -73.0, 40.8, -73.4) + haversine_km(40.8, -73.4, 40.6, -73.8)
    )
    assert stats.distance_nm == pytest.approx(stats.distance_km / 1.852)
    assert stats.max_altitude_ft == 30000
    assert stats.min_altitude_ft == 1000
    assert stats.average_ground_speed_kt == pytest.approx((460 + 420 + 180) / 3)


def test_track_stats_duration_survives_the_newest_first_ordering() -> None:
    """``history.track()`` returns positions newest first — duration must not go negative."""

    stats = track_stats(NEWEST_FIRST)
    assert stats.duration_seconds == 1200.0
    assert stats.start == datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    assert stats.end == datetime(2026, 8, 12, 10, 20, tzinfo=timezone.utc)


def test_track_stats_mixes_naive_adsb_and_aware_history_timestamps() -> None:
    """ADS-B timestamps are naive; subtracting them from aware ones would raise."""

    stats = track_stats(
        [
            AdsbAircraft(latitude=40.6, longitude=-73.8, last_seen=datetime(2026, 8, 12, 10, 0)),
            {"latitude": 40.8, "longitude": -73.4, "timestamp": "2026-08-12T10:10:00Z"},
        ]
    )
    assert stats.duration_seconds == 600.0


def test_track_stats_skips_points_without_coordinates() -> None:
    stats = track_stats([*NEWEST_FIRST, HistoryPosition(callsign="BAW117")])
    assert stats.point_count == 3


def test_track_stats_of_an_empty_track_is_all_zero_and_none() -> None:
    stats = track_stats([])
    assert stats.point_count == 0
    assert stats.distance_km == 0.0
    assert stats.duration_seconds is None
    assert stats.max_altitude_ft is None
    assert stats.average_ground_speed_kt is None
    assert stats.start is None and stats.end is None


def test_track_stats_without_reported_speed_reports_none() -> None:
    stats = track_stats([{"lat": 40.0, "lon": -73.0}, {"lat": 41.0, "lon": -73.0}])
    assert stats.average_ground_speed_kt is None
    assert stats.distance_km > 100


# ── simplify_track ───────────────────────────────────────────────────────────


def test_simplify_track_drops_collinear_points() -> None:
    straight = [{"lat": 40.0 + step * 0.01, "lon": -73.0} for step in range(50)]
    simplified = simplify_track(straight, tolerance_km=0.5)
    assert len(simplified) == 2
    # The originals come back, not coordinate pairs.
    assert simplified[0] is straight[0]
    assert simplified[-1] is straight[-1]


def test_simplify_track_keeps_the_shape() -> None:
    detour = [(40.0, -73.0), (40.5, -72.0), (41.0, -73.0)]
    assert simplify_track(detour, tolerance_km=0.5) == detour


def test_simplify_track_returns_the_original_objects() -> None:
    """Altitude, speed and time must survive — a coordinate pair cannot be drawn."""

    simplified = simplify_track(NEWEST_FIRST, tolerance_km=100.0)
    assert all(isinstance(item, HistoryPosition) for item in simplified)
    assert simplified[0] is NEWEST_FIRST[0]
    assert simplified[0].altitude_baro == 30000
    assert simplified[0].ground_speed == 460


def test_simplify_track_keeps_endpoints_and_order() -> None:
    simplified = simplify_track(NEWEST_FIRST, tolerance_km=100.0)
    assert point_coords(simplified[0]) == (41.0, -73.0)
    assert point_coords(simplified[-1]) == (40.6, -73.8)
    assert len(simplified) == 2


def test_simplify_track_skips_points_without_coordinates() -> None:
    positions = [*NEWEST_FIRST, HistoryPosition(callsign="BAW117")]
    assert len(simplify_track(positions, tolerance_km=100.0)) == 2


def test_simplify_track_passes_short_tracks_through() -> None:
    assert simplify_track([]) == []
    assert simplify_track([(40.0, -73.0)]) == [(40.0, -73.0)]


def test_simplify_track_rejects_a_negative_tolerance() -> None:
    with pytest.raises(ValueError, match="tolerance_km"):
        simplify_track(NEWEST_FIRST, tolerance_km=-1.0)
