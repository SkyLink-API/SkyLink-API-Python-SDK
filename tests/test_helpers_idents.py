"""Identifier classification and normalisation (helpers §3).

Every case is drawn from a real payload: ``GB-0888`` comes out of
``airports.search/location``, ``4CA1FB`` out of a history envelope (upper case,
unlike the request side), ``BAW117`` out of an ADS-B callsign.
"""

from __future__ import annotations

import pytest

from skylink_api.helpers.idents import (
    classify_airport_code,
    is_icao24,
    is_local_pseudocode,
    normalize_icao24,
    normalize_registration,
    split_flight_number,
)


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("JFK", "iata"),
        ("jfk", "iata"),
        (" LHR ", "iata"),
        ("KJFK", "icao"),
        ("egll", "icao"),
        ("UUEE", "icao"),
        ("GB-0888", "local"),
        ("US-0123", "local"),
        ("JF", "unknown"),
        ("KJFKX", "unknown"),
        ("1234", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_classify_airport_code(code: str | None, kind: str) -> None:
    assert classify_airport_code(code) == kind


@pytest.mark.parametrize("code", ["GB-0888", "us-0123", " FR-0042 "])
def test_is_local_pseudocode_spots_ouraiports_codes(code: str) -> None:
    """``airports.search`` cannot resolve these — filter them before joining."""

    assert is_local_pseudocode(code) is True


@pytest.mark.parametrize("code", ["KJFK", "JFK", "", None, "K-J", "USA-0123"])
def test_is_local_pseudocode_rejects_everything_else(code: str | None) -> None:
    assert is_local_pseudocode(code) is False


@pytest.mark.parametrize("value", ["4ca1d3", "4CA1FB", " abcdef "])
def test_is_icao24_accepts_six_hex(value: str) -> None:
    assert is_icao24(value) is True


@pytest.mark.parametrize("value", ["G-STBA", "4ca1d", "4ca1d33", "zzzzzz", "~4ca1d3", "", None])
def test_is_icao24_rejects_anything_else(value: str | None) -> None:
    assert is_icao24(value) is False


def test_normalize_icao24_lowercases_the_response_form() -> None:
    """History and ADS-B echo ``icao24`` upper case; queries want it lower case."""

    assert normalize_icao24("4CA1FB") == "4ca1fb"
    assert normalize_icao24("  4ca1fb ") == "4ca1fb"


def test_normalize_icao24_strips_the_non_icao_marker() -> None:
    assert normalize_icao24("~4CA1FB") == "4ca1fb"


@pytest.mark.parametrize("value", ["G-STBA", "", None, "4ca1", "zzzzzz"])
def test_normalize_icao24_returns_none_for_non_addresses(value: str | None) -> None:
    assert normalize_icao24(value) is None


def test_normalize_registration_upper_cases_and_trims() -> None:
    assert normalize_registration("g-stba") == "G-STBA"
    assert normalize_registration("  n123ab  ") == "N123AB"
    assert normalize_registration("g stba") == "G STBA"
    assert normalize_registration("") is None
    assert normalize_registration(None) is None


@pytest.mark.parametrize(
    ("value", "airline", "number", "kind"),
    [
        ("BA1403", "BA", "1403", "iata"),
        ("ba1403", "BA", "1403", "iata"),
        ("BA 1403", "BA", "1403", "iata"),
        ("BA-1403", "BA", "1403", "iata"),
        ("U21234", "U2", "1234", "iata"),  # the digit-in-the-code case
        ("LH400", "LH", "400", "iata"),
        ("BAW175", "BAW", "175", "icao"),
        ("DLH400", "DLH", "400", "icao"),
        ("BAW117", "BAW", "117", "icao"),
        ("BA0117", "BA", "0117", "iata"),  # leading zero kept
    ],
)
def test_split_flight_number(value: str, airline: str, number: str, kind: str) -> None:
    parsed = split_flight_number(value)
    assert parsed is not None
    assert parsed.airline == airline
    assert parsed.number == number
    assert parsed.kind == kind
    assert tuple(parsed) == (airline, number, kind)


@pytest.mark.parametrize("value", ["", None, "N123AB", "1234", "BRITISH", "B"])
def test_split_flight_number_gives_up_on_non_designators(value: str | None) -> None:
    assert split_flight_number(value) is None
