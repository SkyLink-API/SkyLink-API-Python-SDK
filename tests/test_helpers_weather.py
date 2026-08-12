"""Derived weather values (helpers §4).

Driven from the two parsed fixtures rather than hand-made dicts, so the shapes
are exactly what ``routers/weather.py:_serialize_metar`` / ``_serialize_taf``
emit — including the trap this module exists for: a ``TafPeriod`` has no
``temperature`` and no ``altimeter``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from skylink_api.helpers.weather import (
    ceiling_ft,
    flight_category,
    is_stale,
    metar_age,
    wind_components,
)
from skylink_api.models.weather import MetarWithParsed, TafWithParsed

NOW = datetime(2025, 9, 27, 19, 51, tzinfo=timezone.utc)


@pytest.fixture
def metar(fixture: Any) -> MetarWithParsed:
    return MetarWithParsed.model_validate(fixture("weather_metar_parsed"))


@pytest.fixture
def taf(fixture: Any) -> TafWithParsed:
    return TafWithParsed.model_validate(fixture("weather_taf_parsed"))


# ── ceiling ──────────────────────────────────────────────────────────────────


def test_ceiling_ft_ignores_few_and_scattered(metar: MetarWithParsed) -> None:
    """FEW024 is not a ceiling — only BKN/OVC/VV are."""

    assert ceiling_ft(metar) is None


def test_ceiling_ft_converts_hundreds_of_feet(taf: TafWithParsed) -> None:
    assert taf.parsed is not None
    tempo = taf.parsed.forecast[1]
    assert ceiling_ft(tempo) == 200  # OVC002


def test_ceiling_ft_takes_the_lowest_layer() -> None:
    source = {
        "clouds": [
            {"type": "SCT", "base": 8},
            {"type": "OVC", "base": 30},
            {"type": "BKN", "base": 12},
        ]
    }
    assert ceiling_ft(source) == 1200


def test_ceiling_ft_falls_back_to_the_raw_group() -> None:
    assert ceiling_ft({"clouds": [{"type": "BKN", "base": None, "repr": "BKN018"}]}) == 1800


def test_ceiling_ft_accepts_a_base_already_in_feet() -> None:
    assert ceiling_ft({"clouds": [{"type": "OVC", "base": 2500}]}) == 2500


@pytest.mark.parametrize("source", [None, {}, {"clouds": []}, {"clouds": None}, "CAVOK"])
def test_ceiling_ft_returns_none_without_clouds(source: object) -> None:
    assert ceiling_ft(source) is None


# ── flight category ──────────────────────────────────────────────────────────


def test_flight_category_of_a_clear_metar(metar: MetarWithParsed) -> None:
    assert flight_category(metar) == "VFR"


def test_flight_category_accepts_the_decoded_block_directly(metar: MetarWithParsed) -> None:
    assert flight_category(metar.parsed) == "VFR"


def test_flight_category_works_on_a_taf_period_without_temperature(taf: TafWithParsed) -> None:
    """``TafPeriod`` has no ``temperature``/``altimeter`` — must not blow up."""

    assert taf.parsed is not None
    first, tempo = taf.parsed.forecast
    assert not hasattr(first, "temperature") or first.temperature is None  # type: ignore[attr-defined]
    assert flight_category(first) == "VFR"  # P6SM + SCT035
    assert flight_category(tempo) == "LIFR"  # 1/2SM + OVC002


def test_flight_category_reads_the_p6_visibility_sentinel() -> None:
    """``{"value": null, "repr": "P6"}`` is 6 SM or more, i.e. VFR."""

    assert flight_category({"visibility": {"value": None, "repr": "P6"}}) == "VFR"


@pytest.mark.parametrize(
    ("visibility", "clouds", "expected"),
    [
        ("10SM", [{"type": "FEW", "base": 24}], "VFR"),
        ("6SM", [{"type": "BKN", "base": 40}], "VFR"),
        ("5SM", [], "MVFR"),
        ("10SM", [{"type": "OVC", "base": 30}], "MVFR"),
        ("2SM", [], "IFR"),
        ("10SM", [{"type": "BKN", "base": 9}], "IFR"),
        ("1/2SM", [], "LIFR"),
        ("10SM", [{"type": "VV", "base": 2}], "LIFR"),
        ("3SM", [{"type": "OVC", "base": 4}], "LIFR"),  # worst criterion wins
    ],
)
def test_flight_category_thresholds(
    visibility: str, clouds: list[dict[str, Any]], expected: str
) -> None:
    assert flight_category({"visibility": visibility, "clouds": clouds}) == expected


def test_flight_category_needs_at_least_one_criterion() -> None:
    """No visibility and no ceiling is "unknown", never "VFR"."""

    assert flight_category({}) is None
    assert flight_category(None) is None
    assert flight_category({"temperature": 15}) is None


def test_flight_category_works_with_only_a_ceiling() -> None:
    assert flight_category({"clouds": [{"type": "OVC", "base": 9}]}) == "IFR"
    assert flight_category({"clouds": [{"type": "OVC", "base": 4}]}) == "LIFR"


# ── age / staleness ──────────────────────────────────────────────────────────


def test_metar_age_uses_the_decoded_observation_time(metar: MetarWithParsed) -> None:
    assert metar_age(metar, now=NOW) == timedelta(hours=1)


def test_metar_age_resolves_the_raw_day_hour_group() -> None:
    """``parsed.time`` is ``"271851Z"`` whenever the decoder could not date it."""

    age = metar_age({"parsed": {"time": "271851Z"}}, now=NOW)
    assert age == timedelta(hours=1)


def test_metar_age_falls_back_to_the_envelope_timestamp() -> None:
    age = metar_age({"timestamp": "2025-09-27T18:51:00Z"}, now=NOW)
    assert age == timedelta(hours=1)


def test_metar_age_treats_a_naive_now_as_utc(metar: MetarWithParsed) -> None:
    assert metar_age(metar, now=datetime(2025, 9, 27, 19, 51)) == timedelta(hours=1)


def test_metar_age_is_none_without_any_time() -> None:
    assert metar_age({"raw": "METAR KJFK"}) is None
    assert metar_age(None) is None


def test_is_stale_uses_a_ninety_minute_default(metar: MetarWithParsed) -> None:
    assert is_stale(metar, now=NOW) is False
    assert is_stale(metar, now=NOW + timedelta(hours=1)) is True


def test_is_stale_honours_max_age(metar: MetarWithParsed) -> None:
    assert is_stale(metar, max_age=timedelta(minutes=30), now=NOW) is True
    assert is_stale(metar, max_age=timedelta(hours=3), now=NOW) is False


def test_is_stale_treats_an_undatable_report_as_stale() -> None:
    assert is_stale({"raw": "METAR KJFK"}) is True


# ── wind components ──────────────────────────────────────────────────────────


def test_wind_components_straight_down_the_runway() -> None:
    components = wind_components(160, 160, 13)
    assert components is not None
    assert components.headwind_kt == pytest.approx(13.0)
    assert components.crosswind_kt == pytest.approx(0.0, abs=1e-9)


def test_wind_components_reports_a_tailwind_as_negative() -> None:
    components = wind_components(40, 220, 20)
    assert components is not None
    assert components.headwind_kt == pytest.approx(-20.0)


def test_wind_components_from_the_right() -> None:
    components = wind_components(90, 180, 20)
    assert components is not None
    assert components.crosswind_kt == pytest.approx(20.0)
    assert components.headwind_kt == pytest.approx(0.0, abs=1e-9)
    assert components.from_right is True


def test_wind_components_from_the_left() -> None:
    components = wind_components(90, 0, 20)
    assert components is not None
    assert components.crosswind_kt == pytest.approx(20.0)
    assert components.from_right is False


def test_wind_components_accepts_strings_from_csv_columns() -> None:
    """Enriched-airport columns (``le_heading_degT``) arrive as strings."""

    assert wind_components("40.0", "70", "10") == wind_components(40.0, 70.0, 10.0)


@pytest.mark.parametrize(
    ("heading", "direction", "speed"),
    [(None, 160, 13), (160, None, 13), (160, 160, None), (160, "VRB", 13)],
)
def test_wind_components_is_none_when_a_component_is_unreadable(
    heading: object, direction: object, speed: object
) -> None:
    """A variable (``VRB``) or missing direction has no components.

    ``None`` rather than an exception: ``direction: null`` is what a variable
    wind looks like on the wire, i.e. normal data, not a caller mistake. Zeros
    would read as "calm", which is a different (and dangerous) statement.
    """

    assert wind_components(heading, direction, speed) is None  # type: ignore[arg-type]


def test_wind_components_of_a_variable_wind_metar_block() -> None:
    """The shape the API actually sends for VRB: a direction of ``None``."""

    wind = {"direction": None, "speed": 8, "repr": "VRB08KT"}
    assert wind_components(90, wind["direction"], wind["speed"]) is None  # type: ignore[arg-type]
