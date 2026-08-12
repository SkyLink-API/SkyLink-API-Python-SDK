"""Unit conversions and the parsers for unit-less API values (helpers §1).

No network, no fixtures: every case here is a value the live API was observed to
send (see ``research/04``), fed straight into the helper.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from skylink_api.helpers import units
from skylink_api.helpers.units import (
    c_to_f,
    f_to_c,
    fpm_to_ms,
    ft_to_m,
    hpa_to_inhg,
    humidity_to_percent,
    inhg_to_hpa,
    km_to_nm,
    kt_to_kmh,
    kt_to_mph,
    kt_to_ms,
    m_to_ft,
    nm_to_km,
    normalize_altimeter,
    parse_duration,
    parse_duration_minutes,
    parse_visibility,
    sm_to_km,
    to_number,
)
from skylink_api.models.weather import Visibility as WireVisibility

CONVERTERS = [
    ft_to_m,
    m_to_ft,
    kt_to_kmh,
    kt_to_ms,
    kt_to_mph,
    nm_to_km,
    km_to_nm,
    sm_to_km,
    inhg_to_hpa,
    hpa_to_inhg,
    c_to_f,
    f_to_c,
    fpm_to_ms,
]


# ── to_number ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1.0),
        (1.5, 1.5),
        ("119.1", 119.1),  # frequency_mhz arrives as a string
        ("  2019 ", 2019.0),  # year_built
        ("1", 1.0),  # Runway.lighted
        ("-3", -3.0),
    ],
)
def test_to_number_reads_numbers_and_stringly_numbers(value: object, expected: float) -> None:
    assert to_number(value) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, "", "   ", "Y", "n/a", True, False, [], {}, "nan", "inf"])
def test_to_number_returns_none_for_anything_else(value: object) -> None:
    """``active: "Y"`` and friends must not become numbers — and must not raise."""

    assert to_number(value) is None  # type: ignore[arg-type]


# ── converters ───────────────────────────────────────────────────────────────


def test_length_conversions_round_trip() -> None:
    assert ft_to_m(1000) == pytest.approx(304.8)
    assert m_to_ft(304.8) == pytest.approx(1000.0)
    assert m_to_ft(ft_to_m(35000)) == pytest.approx(35000.0)


def test_speed_conversions() -> None:
    assert kt_to_kmh(100) == pytest.approx(185.2)
    assert kt_to_ms(100) == pytest.approx(51.44444, rel=1e-5)
    assert kt_to_mph(100) == pytest.approx(115.0779, rel=1e-5)


def test_distance_conversions() -> None:
    assert nm_to_km(100) == pytest.approx(185.2)
    assert km_to_nm(185.2) == pytest.approx(100.0)
    assert sm_to_km(10) == pytest.approx(16.09344)


def test_pressure_conversions() -> None:
    assert inhg_to_hpa(29.92) == pytest.approx(1013.21, abs=0.01)
    assert hpa_to_inhg(1013.25) == pytest.approx(29.9213, abs=1e-4)


def test_temperature_conversions() -> None:
    assert c_to_f(0) == 32.0
    assert c_to_f(-40) == -40.0
    assert f_to_c(212) == pytest.approx(100.0)


def test_vertical_rate_conversion() -> None:
    assert fpm_to_ms(1000) == pytest.approx(5.08)
    assert fpm_to_ms(-1500) == pytest.approx(-7.62)


@pytest.mark.parametrize("converter", CONVERTERS)
def test_every_converter_accepts_strings(converter: object) -> None:
    """Numeric columns are strings about as often as numbers."""

    assert converter("10") is not None  # type: ignore[operator]


@pytest.mark.parametrize("converter", CONVERTERS)
@pytest.mark.parametrize("value", [None, "", "unknown", True])
def test_every_converter_returns_none_instead_of_raising(converter: object, value: object) -> None:
    assert converter(value) is None  # type: ignore[operator]


def test_conversion_factors_are_exported_as_constants() -> None:
    assert units.METERS_PER_FOOT == 0.3048
    assert units.KM_PER_NAUTICAL_MILE == 1.852
    assert units.KM_PER_STATUTE_MILE == 1.609344


# ── normalize_altimeter ──────────────────────────────────────────────────────


def test_normalize_altimeter_reads_a_us_setting_as_inches() -> None:
    """The live API sends ``altimeter`` with no unit; 29.92 can only be inHg."""

    altimeter = normalize_altimeter(29.92)
    assert altimeter is not None
    assert altimeter.unit == "inHg"
    assert altimeter.in_hg == 29.92
    assert altimeter.hpa == pytest.approx(1013.21, abs=0.01)


def test_normalize_altimeter_reads_a_european_setting_as_hectopascals() -> None:
    altimeter = normalize_altimeter("1013")
    assert altimeter is not None
    assert altimeter.unit == "hPa"
    assert altimeter.hpa == 1013.0
    assert altimeter.in_hg == pytest.approx(29.9139, abs=1e-4)


@pytest.mark.parametrize("value", [40, 100, 499.9, 0, -5, None, "", "high"])
def test_normalize_altimeter_refuses_to_guess(value: object) -> None:
    """Between 40 and 500 the unit is genuinely ambiguous — ``None`` beats a guess."""

    assert normalize_altimeter(value) is None  # type: ignore[arg-type]


# ── humidity ─────────────────────────────────────────────────────────────────


def test_humidity_to_percent_scales_the_fraction() -> None:
    """``relative_humidity`` is a fraction (0.62), not a percentage."""

    assert humidity_to_percent(0.62) == pytest.approx(62.0)
    assert humidity_to_percent("0.5") == pytest.approx(50.0)
    assert humidity_to_percent(1) == pytest.approx(100.0)


def test_humidity_to_percent_is_idempotent_on_real_percentages() -> None:
    assert humidity_to_percent(62.0) == 62.0
    assert humidity_to_percent(humidity_to_percent(0.62)) == pytest.approx(62.0)


def test_humidity_to_percent_ignores_junk() -> None:
    assert humidity_to_percent(None) is None
    assert humidity_to_percent("wet") is None


# ── durations ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("7h 23m", 443.0),  # ml.flight_time estimated_hours_display
        ("45m", 45.0),
        ("2h", 120.0),
        ("1 h 5 min", 65.0),
        ("3 hours", 180.0),
        ("90 minutes", 90.0),
        ("1h30m30s", 90.5),
        ("7:23", 443.0),
        ("PT7H23M", 443.0),
        (45, 45.0),
    ],
)
def test_parse_duration_minutes(text: object, minutes: float) -> None:
    assert parse_duration_minutes(text) == pytest.approx(minutes)  # type: ignore[arg-type]


@pytest.mark.parametrize("text", [None, "", "soon", "on time"])
def test_parse_duration_minutes_gives_up_quietly(text: object) -> None:
    assert parse_duration_minutes(text) is None  # type: ignore[arg-type]


def test_parse_duration_returns_a_timedelta() -> None:
    assert parse_duration("7h 23m") == timedelta(hours=7, minutes=23)
    assert parse_duration("nope") is None


# ── visibility ───────────────────────────────────────────────────────────────


def test_parse_visibility_handles_the_p6_sentinel() -> None:
    """The live API sends P6SM as ``{"value": null, "repr": "P6"}``."""

    visibility = parse_visibility(WireVisibility(value=None, repr="P6"))
    assert visibility is not None
    assert visibility.at_least is True
    assert visibility.statute_miles == 6.0
    assert visibility.meters == pytest.approx(9656.06, abs=0.01)


def test_parse_visibility_handles_the_p6_sentinel_as_a_dict() -> None:
    assert parse_visibility({"value": None, "repr": "P6"}) == parse_visibility(
        WireVisibility(value=None, repr="P6")
    )


def test_parse_visibility_reads_a_decoded_block() -> None:
    visibility = parse_visibility(WireVisibility(value=10, repr="10SM"))
    assert visibility is not None
    assert visibility.statute_miles == 10.0
    assert visibility.meters == pytest.approx(16093.44)
    assert visibility.at_least is False


def test_parse_visibility_reads_a_minus_group() -> None:
    """``M`` means *less than*: the number is a ceiling, so ``at_least`` stays false."""

    visibility = parse_visibility("M1/4SM")
    assert visibility is not None
    assert visibility.statute_miles == 0.25
    assert visibility.at_least is False


def test_parse_visibility_reads_metric_groups() -> None:
    visibility = parse_visibility("9999")
    assert visibility is not None
    assert visibility.meters == 9999.0
    assert visibility.at_least is True  # 9999 = "10 km or more"
    assert visibility.statute_miles == pytest.approx(6.2131, abs=1e-4)

    metric = parse_visibility("0800")
    assert metric is not None
    assert metric.meters == 800.0
    assert metric.at_least is False


def test_parse_visibility_reads_mixed_fractions() -> None:
    visibility = parse_visibility("1 1/2SM")
    assert visibility is not None
    assert visibility.statute_miles == 1.5


def test_parse_visibility_guesses_the_unit_of_a_bare_number() -> None:
    small = parse_visibility(10)
    large = parse_visibility(4000)
    assert small is not None and small.statute_miles == 10.0
    assert large is not None and large.meters == 4000.0


@pytest.mark.parametrize(
    "value",
    [None, "", "  ", "CAVOK", {"value": None, "repr": None}, {}, True],
)
def test_parse_visibility_returns_none_for_undecodable_input(value: object) -> None:
    assert parse_visibility(value) is None
