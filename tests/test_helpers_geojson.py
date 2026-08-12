"""GeoJSON exporters (helpers §5).

The one invariant worth repeating: GeoJSON positions are ``[longitude,
latitude]`` while every SkyLink payload puts latitude first. Half of these tests
exist to keep that flip in place.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from skylink_api.helpers.geojson import (
    adsb_to_geojson,
    airports_to_geojson,
    navaids_to_geojson,
    track_to_geojson,
)
from skylink_api.models.adsb import AdsbAircraftList
from skylink_api.models.airports import EnrichedAirport
from skylink_api.models.history import HistoryTrackResponse
from skylink_api.models.navaids import NavaidsResponse


@pytest.fixture
def adsb(fixture: Any) -> AdsbAircraftList:
    return AdsbAircraftList.model_validate(fixture("adsb_aircraft"))


@pytest.fixture
def track(fixture: Any) -> HistoryTrackResponse:
    return HistoryTrackResponse.model_validate(fixture("history_track"))


@pytest.fixture
def airport(fixture: Any) -> EnrichedAirport:
    return EnrichedAirport.model_validate(fixture("airports_search"))


# ── the coordinate order ─────────────────────────────────────────────────────


def test_coordinates_are_lon_lat_not_lat_lon(adsb: AdsbAircraftList) -> None:
    """The single most common GeoJSON bug — asserted explicitly."""

    feature = adsb_to_geojson(adsb)["features"][0]
    longitude, latitude = feature["geometry"]["coordinates"]
    assert (latitude, longitude) == (51.47, -0.4543)
    assert longitude == -0.4543


def test_every_exporter_puts_longitude_first(
    adsb: AdsbAircraftList, track: HistoryTrackResponse, airport: EnrichedAirport
) -> None:
    collections = [
        adsb_to_geojson(adsb),
        airports_to_geojson(airport),
        navaids_to_geojson([{"latitude_deg": 40.63, "longitude_deg": -73.77, "ident": "JFK"}]),
    ]
    for collection in collections:
        for feature in collection["features"]:
            longitude, latitude = feature["geometry"]["coordinates"]
            assert -180.0 <= longitude <= 180.0
            assert -90.0 <= latitude <= 90.0
    line = track_to_geojson(track)["features"][0]
    for longitude, latitude in line["geometry"]["coordinates"]:
        assert -180.0 <= longitude <= 180.0
        assert -90.0 <= latitude <= 90.0


# ── ADS-B ────────────────────────────────────────────────────────────────────


def test_adsb_to_geojson_shape(adsb: AdsbAircraftList) -> None:
    collection = adsb_to_geojson(adsb)
    assert collection["type"] == "FeatureCollection"
    feature = collection["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Point"
    assert feature["properties"]["icao24"] == "4ca1fb"
    assert feature["properties"]["callsign"] == "BAW117"
    assert feature["properties"]["altitude"] == 36000


def test_adsb_to_geojson_skips_aircraft_without_a_position(adsb: AdsbAircraftList) -> None:
    """The fixture's second aircraft is Mode S only — no feature at (0, 0)."""

    assert len(adsb.aircraft) == 2
    assert len(adsb_to_geojson(adsb)["features"]) == 1


def test_adsb_to_geojson_drops_null_properties(adsb: AdsbAircraftList) -> None:
    properties = adsb_to_geojson(adsb)["features"][0]["properties"]
    assert None not in properties.values()


def test_adsb_to_geojson_accepts_a_bare_list(adsb: AdsbAircraftList) -> None:
    assert adsb_to_geojson(list(adsb.aircraft)) == adsb_to_geojson(adsb)


def test_adsb_to_geojson_serialises_timestamps(adsb: AdsbAircraftList) -> None:
    properties = adsb_to_geojson(adsb)["features"][0]["properties"]
    assert properties["last_seen"] == "2026-02-11T12:00:03.412870"
    assert json.dumps(adsb_to_geojson(adsb))  # the whole thing stays JSON-safe


def test_adsb_to_geojson_of_nothing_is_an_empty_collection() -> None:
    assert adsb_to_geojson([]) == {"type": "FeatureCollection", "features": []}


# ── tracks ───────────────────────────────────────────────────────────────────


def test_track_to_geojson_builds_one_line(track: HistoryTrackResponse) -> None:
    collection = track_to_geojson(track)
    assert len(collection["features"]) == 1
    feature = collection["features"][0]
    assert feature["geometry"]["type"] == "LineString"
    assert len(feature["geometry"]["coordinates"]) == 3
    assert feature["properties"]["callsign"] == "BAW117"
    assert feature["properties"]["flight_id"] == track.flight_id
    assert feature["properties"]["point_count"] == 3


def test_track_to_geojson_reorders_the_newest_first_payload(track: HistoryTrackResponse) -> None:
    """``history.track()`` returns positions newest first; a line should start at takeoff."""

    coordinates = track_to_geojson(track)["features"][0]["geometry"]["coordinates"]
    assert coordinates[0] == [-0.4619, 51.4706]  # EGLL, the oldest sample
    assert coordinates[-1] == [-73.7801, 40.6421]  # KJFK, the newest


def test_track_to_geojson_can_add_the_points(track: HistoryTrackResponse) -> None:
    collection = track_to_geojson(track, include_points=True)
    assert len(collection["features"]) == 4
    assert collection["features"][0]["geometry"]["type"] == "LineString"
    oldest = collection["features"][1]
    assert oldest["geometry"]["type"] == "Point"
    assert oldest["properties"]["timestamp"] == "2026-02-10T08:55:05+00:00"
    assert "altitude_baro" not in oldest["properties"]  # null in the fixture
    newest = collection["features"][-1]
    assert newest["properties"]["altitude_baro"] == 0  # a zero is kept, unlike a null


def test_track_to_geojson_accepts_a_bare_position_list(track: HistoryTrackResponse) -> None:
    collection = track_to_geojson(list(track.positions))
    assert collection["features"][0]["geometry"]["type"] == "LineString"
    assert "flight_id" not in collection["features"][0]["properties"]


def test_track_to_geojson_refuses_a_one_point_line() -> None:
    """A LineString needs two positions; one point alone is invalid GeoJSON."""

    single = [{"latitude": 40.0, "longitude": -73.0}]
    assert track_to_geojson(single)["features"] == []
    assert len(track_to_geojson(single, include_points=True)["features"]) == 1


# ── airports and navaids ─────────────────────────────────────────────────────


def test_airports_to_geojson_of_a_single_enriched_airport(airport: EnrichedAirport) -> None:
    collection = airports_to_geojson(airport)
    assert len(collection["features"]) == 1
    feature = collection["features"][0]
    assert feature["geometry"]["coordinates"] == [-73.77890015, 40.63980103]
    assert feature["properties"]["ident"] == "KJFK"
    assert feature["properties"]["iata_code"] == "JFK"
    assert feature["properties"]["elevation_ft"] == 13


def test_airports_to_geojson_of_a_search_envelope() -> None:
    envelope = {
        "search_location": {"latitude": 40.6, "longitude": -73.8},
        "airports": [
            {"ident": "KJFK", "latitude_deg": 40.63, "longitude_deg": -73.77, "distance_km": 4.2},
            {"ident": "GB-0888", "latitude_deg": None, "longitude_deg": None},
        ],
        "airports_found": 2,
    }
    collection = airports_to_geojson(envelope)
    assert len(collection["features"]) == 1
    assert collection["features"][0]["properties"]["distance_km"] == 4.2


def test_navaids_to_geojson_keeps_the_frequency_verbatim(fixture: Any) -> None:
    """``frequency_khz`` is a string inside an airport payload, a number from /navaids."""

    envelope = NavaidsResponse.model_validate(
        {
            "navaids": [
                {
                    "ident": "JFK",
                    "name": "Kennedy",
                    "type": "VOR-DME",
                    "frequency_khz": 115900,
                    "latitude_deg": 40.63,
                    "longitude_deg": -73.77,
                    "usageType": "BOTH",
                }
            ],
            "total": 1,
        }
    )
    feature = navaids_to_geojson(envelope)["features"][0]
    assert feature["properties"]["frequency_khz"] == 115900
    assert feature["properties"]["usage_type"] == "BOTH"
    assert feature["geometry"]["coordinates"] == [-73.77, 40.63]

    embedded = navaids_to_geojson(fixture("airports_search")["navaids"])
    assert embedded["features"] == []  # the embedded rows carry no coordinates


def test_exporters_survive_junk_input() -> None:
    for collection in (
        adsb_to_geojson(None),
        track_to_geojson(None),
        airports_to_geojson({}),
        navaids_to_geojson("nope"),
    ):
        assert collection == {"type": "FeatureCollection", "features": []}
