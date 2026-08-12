"""``skylink_api.pandas_ext.to_dataframe`` (§11 interop).

pandas is an optional extra, so the whole module is skipped when it is absent —
the SDK itself must keep working without it, and that is asserted too (the
import-error message is checked with pandas masked out of ``sys.modules``).

What matters here is not pandas: it is that the *right list* is found inside
each envelope, that nothing is coerced on the way, and that a response the
function cannot read fails with a message naming what it tried.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

pd = pytest.importorskip("pandas", reason="optional extra: pip install skylink-api[pandas]")

from skylink_api.models.adsb import AdsbAircraftList  # noqa: E402
from skylink_api.models.geo import CountriesResponse, RegionsResponse  # noqa: E402
from skylink_api.models.history import (  # noqa: E402
    HistoryFlightsResponse,
    HistoryPositionsResponse,
    HistoryTrackResponse,
)
from skylink_api.models.navaids import NavaidsResponse  # noqa: E402
from skylink_api.models.notams import NotamsResponse  # noqa: E402
from skylink_api.models.routes import VrsRouteResult  # noqa: E402
from skylink_api.models.schedules import DeparturesResponse  # noqa: E402
from skylink_api.models.tickets import TicketSearchResponse  # noqa: E402
from skylink_api.models.weather import Metar  # noqa: E402
from skylink_api.pandas_ext import LIST_FIELDS, to_dataframe  # noqa: E402

COUNTRIES: dict[str, Any] = {
    "count": 3,
    "countries": [
        {"code": "GB", "name": "United Kingdom", "continent": "EU"},
        {"code": "FR", "name": "France", "continent": "EU"},
        {"code": "US", "name": "United States", "continent": None},
    ],
}

REGIONS: dict[str, Any] = {
    "count": 2,
    "country": "GB",
    "regions": [
        {"code": "GB-ENG", "local_code": "ENG", "name": "England", "iso_country": "GB"},
        {"code": "GB-SCT", "local_code": "SCT", "name": "Scotland", "iso_country": "GB"},
    ],
}

NAVAIDS: dict[str, Any] = {
    "total": 2,
    "filters_applied": {"country": "GB"},
    "navaids": [
        {
            "id": 1,
            "ident": "LON",
            "type": "VOR-DME",
            # A string on the enriched-airport form of the same model — the SDK
            # never coerces it, so the column must stay ``object``.
            "frequency_khz": "113600",
            "latitude_deg": 51.5,
            "longitude_deg": -0.45,
            "usageType": "BOTH",
        },
        {
            "id": 2,
            "ident": "BIG",
            "type": "VOR-DME",
            "frequency_khz": 115100,
            "latitude_deg": 51.33,
            "longitude_deg": 0.03,
            "usageType": "HI",
        },
    ],
}

POSITIONS_AND_FLIGHTS: dict[str, Any] = {
    "count": 2,
    "positions": [
        {"latitude": 51.4, "longitude": -0.4, "altitude": 3000},
        {"latitude": 51.5, "longitude": -0.3, "altitude": 5000},
    ],
    "flights": [{"flight_id": "abc", "callsign": "BAW117"}],
}


@pytest.fixture
def adsb(fixture: Any) -> AdsbAircraftList:
    return AdsbAircraftList.model_validate(fixture("adsb_aircraft"))


# ── the envelopes ────────────────────────────────────────────────────────────


def test_adsb_envelope_unwraps_the_aircraft_list(adsb: AdsbAircraftList) -> None:
    frame = to_dataframe(adsb)

    assert len(frame) == len(adsb.aircraft)
    assert "icao24" in frame.columns
    # The envelope's own counters must not leak in as columns.
    assert "total_count" not in frame.columns


def test_rows_keep_api_order(adsb: AdsbAircraftList) -> None:
    assert list(frame_column(to_dataframe(adsb), "icao24")) == [
        plane.icao24 for plane in adsb.aircraft
    ]


def test_schedules_board_unwraps_flights_with_snake_case_columns(fixture: Any) -> None:
    """Board rows are PascalCase on the wire; the model exposes snake_case."""

    board = DeparturesResponse.model_validate(fixture("schedules_departures"))

    frame = to_dataframe(board)

    assert len(frame) == 1
    assert {"flight", "time", "destination"} <= set(frame.columns)
    assert "Flight" not in frame.columns
    assert frame.loc[0, "flight"] == "C84093"


def test_history_flights_envelope(fixture: Any) -> None:
    response = HistoryFlightsResponse.model_validate(fixture("history_flights"))

    frame = to_dataframe(response)

    assert len(frame) == response.count
    assert "callsign" in frame.columns


def test_history_track_uses_positions(fixture: Any) -> None:
    track = HistoryTrackResponse.model_validate(fixture("history_track"))

    frame = to_dataframe(track)

    assert len(frame) == len(track.positions)
    assert {"latitude", "longitude"} <= set(frame.columns)


def test_positions_win_over_flights_when_a_response_carries_both() -> None:
    """``history.positions()`` returns both lists; positions are the rows."""

    response = HistoryPositionsResponse.model_validate(POSITIONS_AND_FLIGHTS)

    frame = to_dataframe(response)

    assert len(frame) == 2
    assert "altitude" in frame.columns


def test_field_argument_selects_the_other_list() -> None:
    response = HistoryPositionsResponse.model_validate(POSITIONS_AND_FLIGHTS)

    frame = to_dataframe(response, field="flights")

    assert len(frame) == 1
    assert frame.loc[0, "callsign"] == "BAW117"


def test_navaids_envelope() -> None:
    frame = to_dataframe(NavaidsResponse.model_validate(NAVAIDS))

    assert list(frame_column(frame, "ident")) == ["LON", "BIG"]
    assert "usage_type" in frame.columns  # the alias resolves to the attribute name


def test_countries_envelope() -> None:
    frame = to_dataframe(CountriesResponse.model_validate(COUNTRIES))

    assert len(frame) == 3
    assert list(frame_column(frame, "code")) == ["GB", "FR", "US"]


def test_regions_envelope() -> None:
    frame = to_dataframe(RegionsResponse.model_validate(REGIONS))

    assert list(frame_column(frame, "local_code")) == ["ENG", "SCT"]


def test_tickets_envelope_keeps_nested_legs_as_objects(fixture: Any) -> None:
    offers = TicketSearchResponse.model_validate(fixture("tickets_search"))

    frame = to_dataframe(offers)

    assert len(frame) == len(offers.flights)
    assert "price_usd" in frame.columns
    # Nested lists stay nested — one row is one offer, not one leg.
    assert isinstance(frame.loc[0, "legs"], list)
    assert isinstance(frame.loc[0, "legs"][0], dict)


def test_notams_envelope(fixture: Any) -> None:
    frame = to_dataframe(NotamsResponse.model_validate(fixture("notams")))

    assert len(frame) > 0
    assert "notam_id" in frame.columns or "text" in frame.columns


def test_every_documented_field_name_is_reachable() -> None:
    """Each name in ``LIST_FIELDS`` is unwrapped when it holds records."""

    for name in LIST_FIELDS:
        frame = to_dataframe({name: [{"a": 1}, {"a": 2}]})
        assert list(frame_column(frame, "a")) == [1, 2], name


# ── bare lists ───────────────────────────────────────────────────────────────


def test_bare_list_of_models(adsb: AdsbAircraftList) -> None:
    frame = to_dataframe(adsb.aircraft)

    assert len(frame) == len(adsb.aircraft)
    assert "callsign" in frame.columns


def test_bare_list_of_dicts_from_a_raw_payload(fixture: Any) -> None:
    """``client.request()`` hands back plain JSON — that works too."""

    payload = fixture("adsb_aircraft")

    assert len(to_dataframe(payload["aircraft"])) == len(payload["aircraft"])
    assert len(to_dataframe(payload)) == len(payload["aircraft"])


def test_any_iterable_of_records_works(adsb: AdsbAircraftList) -> None:
    """A poller diff's ``snapshot.values()`` and generators, not just lists."""

    snapshot = {plane.icao24: plane for plane in adsb.aircraft}

    assert len(to_dataframe(snapshot.values())) == len(snapshot)
    assert len(to_dataframe(plane for plane in adsb.aircraft)) == len(adsb.aircraft)


def test_empty_response_gives_an_empty_frame() -> None:
    frame = to_dataframe(AdsbAircraftList.model_validate({"aircraft": [], "total_count": 0}))

    assert frame.empty
    assert len(frame.columns) == 0


def test_empty_bare_list() -> None:
    assert to_dataframe([]).empty


# ── what is not converted ────────────────────────────────────────────────────


def test_string_numbers_stay_strings() -> None:
    """``frequency_khz: "113600"`` is a string on the wire and stays one."""

    frame = to_dataframe(NavaidsResponse.model_validate(NAVAIDS))

    assert frame["frequency_khz"].dtype == object
    assert frame.loc[0, "frequency_khz"] == "113600"
    assert frame.loc[1, "frequency_khz"] == 115100


def test_unknown_backend_fields_become_columns() -> None:
    """``extra="allow"`` means a new upstream field shows up, not disappears."""

    response = NavaidsResponse.model_validate(
        {"navaids": [{"ident": "LON", "brand_new_column": "surprise"}], "total": 1}
    )

    frame = to_dataframe(response)

    assert frame.loc[0, "brand_new_column"] == "surprise"


def test_missing_keys_become_nan_not_an_error() -> None:
    frame = to_dataframe([{"a": 1}, {"b": 2}])

    assert set(frame.columns) == {"a", "b"}
    assert bool(frame["a"].isna().iloc[1])


# ── failure modes ────────────────────────────────────────────────────────────


def test_single_record_response_is_rejected_with_a_helpful_message(fixture: Any) -> None:
    metar = Metar.model_validate(fixture("weather_metar"))

    with pytest.raises(TypeError) as err:
        to_dataframe(metar)

    message = str(err.value)
    assert "Metar" in message
    assert "aircraft" in message and "countries" in message  # the tried names
    assert "field=" in message


def test_list_of_scalars_is_not_a_frame() -> None:
    """``VrsRouteResult.airports`` is a list of ICAO strings, not rows."""

    route = VrsRouteResult.model_validate(
        {"source": "vrs", "callsign": "BAW117", "airports": ["EGLL", "KJFK"]}
    )

    with pytest.raises(TypeError):
        to_dataframe(route)

    with pytest.raises(TypeError):
        to_dataframe(["EGLL", "KJFK"])


def test_mixed_list_is_rejected() -> None:
    with pytest.raises(TypeError):
        to_dataframe([{"a": 1}, "EGLL"])


def test_scalar_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        to_dataframe(42)


def test_unknown_field_raises_key_error(adsb: AdsbAircraftList) -> None:
    with pytest.raises(KeyError):
        to_dataframe(adsb, field="nope")


def test_field_that_is_not_a_list_of_records_raises_value_error(adsb: AdsbAircraftList) -> None:
    with pytest.raises(ValueError, match="not a list"):
        to_dataframe(adsb, field="total_count")


def test_import_error_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without pandas the message must say how to get it."""

    monkeypatch.setitem(sys.modules, "pandas", None)

    with pytest.raises(ImportError) as err:
        to_dataframe([{"a": 1}])

    assert 'pip install "skylink-api[pandas]"' in str(err.value)


# ── helpers ──────────────────────────────────────────────────────────────────


def frame_column(frame: Any, name: str) -> Any:
    """``frame[name]`` with a readable failure when the column is missing."""

    assert name in frame.columns, f"missing column {name!r}; got {list(frame.columns)}"
    return frame[name]
