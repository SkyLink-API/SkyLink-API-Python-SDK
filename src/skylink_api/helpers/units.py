"""Unit conversions and tolerant parsers for the values SkyLink puts on the wire.

The API forwards scraped data with almost no normalisation, which means the same
field arrives as a number one day and as a string the next (``frequency_mhz:
"119.1"`` vs ``125.7``, ``lighted: "1"`` vs ``1``). Every converter here
therefore accepts ``float | int | str | None`` and answers ``None`` for anything
it cannot read — a conversion helper that raises would turn a cosmetic backend
change into a crash.

Three parsers exist purely because of live-API traps:

* :func:`normalize_altimeter` — ``altimeter`` is sent **without a unit**, inHg
  and hPa are only distinguishable by magnitude.
* :func:`humidity_to_percent` — ``relative_humidity`` is a **fraction**
  (``0.62``), not a percentage.
* :func:`parse_visibility` — ``P6SM`` arrives as ``{"value": None,
  "repr": "P6"}``, i.e. the only usable information is in the raw token.

This module is also where the two internal accessors used by the rest of
``skylink_api.helpers`` live (:func:`to_number` and ``_attr``), so that every
helper reads pydantic models and bare dicts the same way.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Literal, NamedTuple, TypeAlias

__all__ = [
    "FEET_PER_METER",
    "HPA_PER_INHG",
    "KMH_PER_KNOT",
    "KM_PER_NAUTICAL_MILE",
    "KM_PER_STATUTE_MILE",
    "METERS_PER_FOOT",
    "METERS_PER_STATUTE_MILE",
    "MPH_PER_KNOT",
    "MS_PER_FPM",
    "MS_PER_KNOT",
    "NAUTICAL_MILES_PER_KM",
    "Altimeter",
    "NumberLike",
    "Visibility",
    "c_to_f",
    "f_to_c",
    "fpm_to_ms",
    "ft_to_m",
    "hpa_to_inhg",
    "humidity_to_percent",
    "inhg_to_hpa",
    "km_to_nm",
    "kt_to_kmh",
    "kt_to_mph",
    "kt_to_ms",
    "m_to_ft",
    "nm_to_km",
    "normalize_altimeter",
    "parse_duration",
    "parse_duration_minutes",
    "parse_visibility",
    "sm_to_km",
    "to_number",
]

#: Anything the API may put in a numeric field. Strings are common: several
#: payloads are forwarded straight from CSV columns.
NumberLike: TypeAlias = float | int | str | None

# ── conversion factors (never inline a magic number) ─────────────────────────

#: Metres in one international foot (exact by definition).
METERS_PER_FOOT = 0.3048
#: Feet in one metre.
FEET_PER_METER = 1.0 / METERS_PER_FOOT
#: Kilometres per hour in one knot (exact: 1 nm = 1852 m).
KMH_PER_KNOT = 1.852
#: Metres per second in one knot.
MS_PER_KNOT = 1852.0 / 3600.0
#: Statute miles per hour in one knot.
MPH_PER_KNOT = 1.150779448023543
#: Kilometres in one nautical mile.
KM_PER_NAUTICAL_MILE = 1.852
#: Nautical miles in one kilometre.
NAUTICAL_MILES_PER_KM = 1.0 / KM_PER_NAUTICAL_MILE
#: Kilometres in one statute mile (exact by definition).
KM_PER_STATUTE_MILE = 1.609344
#: Metres in one statute mile.
METERS_PER_STATUTE_MILE = 1609.344
#: Hectopascals in one inch of mercury (0 °C, standard gravity).
HPA_PER_INHG = 33.86388666666671
#: Metres per second in one foot per minute.
MS_PER_FPM = METERS_PER_FOOT / 60.0

#: ``altimeter`` below this is read as inches of mercury (a plausible setting is
#: 27-32 inHg, a plausible hPa reading starts around 900).
_ALTIMETER_INHG_MAX = 40.0
#: ``altimeter`` at or above this is read as hectopascals.
_ALTIMETER_HPA_MIN = 500.0
#: ``relative_humidity`` above this is already a percentage, not a fraction.
_HUMIDITY_FRACTION_MAX = 1.5
#: A bare visibility number at or above this is metres, below it statute miles.
_VISIBILITY_METERS_MIN = 100.0
#: METAR ``9999`` means "10 km or more", not exactly 9999 m.
_VISIBILITY_UNLIMITED_METERS = 9999.0


# ── wire-value access (shared by every helper module) ────────────────────────


def to_number(value: NumberLike) -> float | None:
    """Read a wire value as a ``float``; ``None`` when it is not a finite number.

    Handles the SDK's recurring "number or string" fields (``elevation_ft``,
    ``frequency_mhz``, ``lighted``, ``year_built``). ``bool`` is rejected on
    purpose: ``True`` is not a measurement. ``NaN``/``Infinity`` also come back
    as ``None`` so they cannot poison arithmetic downstream.
    """

    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        # TypeError covers the values that are not even in ``NumberLike`` — a
        # dict or a list reaching here is a wire surprise, not a crash.
        return None
    return number if math.isfinite(number) else None


def _attr(obj: object, *names: str) -> Any:
    """First non-``None`` of ``names`` on ``obj``, read as attribute or key.

    The helpers must work identically on the SDK's pydantic models and on the
    plain dicts people keep around after ``model_dump()`` or ``response.json()``,
    so every field read goes through here. Missing names are not an error.
    """

    if obj is None:
        return None
    for name in names:
        if isinstance(obj, Mapping):
            if name in obj:
                value = obj[name]
                if value is not None:
                    return value
            continue
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


# ── length / altitude ────────────────────────────────────────────────────────


def ft_to_m(value: NumberLike) -> float | None:
    """Feet → metres. ``None`` for a non-numeric input."""

    number = to_number(value)
    return None if number is None else number * METERS_PER_FOOT


def m_to_ft(value: NumberLike) -> float | None:
    """Metres → feet. ``None`` for a non-numeric input."""

    number = to_number(value)
    return None if number is None else number * FEET_PER_METER


def nm_to_km(value: NumberLike) -> float | None:
    """Nautical miles → kilometres (``distance_nm``, ``max_range_nm``)."""

    number = to_number(value)
    return None if number is None else number * KM_PER_NAUTICAL_MILE


def km_to_nm(value: NumberLike) -> float | None:
    """Kilometres → nautical miles (``distance_km`` of a nearby-airport hit)."""

    number = to_number(value)
    return None if number is None else number * NAUTICAL_MILES_PER_KM


def sm_to_km(value: NumberLike) -> float | None:
    """Statute miles → kilometres (US visibility groups are in statute miles)."""

    number = to_number(value)
    return None if number is None else number * KM_PER_STATUTE_MILE


# ── speed ────────────────────────────────────────────────────────────────────


def kt_to_kmh(value: NumberLike) -> float | None:
    """Knots → kilometres per hour (``ground_speed``, ``cruise_speed_ktas``)."""

    number = to_number(value)
    return None if number is None else number * KMH_PER_KNOT


def kt_to_ms(value: NumberLike) -> float | None:
    """Knots → metres per second."""

    number = to_number(value)
    return None if number is None else number * MS_PER_KNOT


def kt_to_mph(value: NumberLike) -> float | None:
    """Knots → statute miles per hour."""

    number = to_number(value)
    return None if number is None else number * MPH_PER_KNOT


def fpm_to_ms(value: NumberLike) -> float | None:
    """Feet per minute → metres per second (``vertical_rate``; negative = descent)."""

    number = to_number(value)
    return None if number is None else number * MS_PER_FPM


# ── pressure ─────────────────────────────────────────────────────────────────


def inhg_to_hpa(value: NumberLike) -> float | None:
    """Inches of mercury → hectopascals."""

    number = to_number(value)
    return None if number is None else number * HPA_PER_INHG


def hpa_to_inhg(value: NumberLike) -> float | None:
    """Hectopascals → inches of mercury."""

    number = to_number(value)
    return None if number is None else number / HPA_PER_INHG


# ── temperature ──────────────────────────────────────────────────────────────


def c_to_f(value: NumberLike) -> float | None:
    """Degrees Celsius → degrees Fahrenheit (``temperature``, ``dewpoint``)."""

    number = to_number(value)
    return None if number is None else number * 9.0 / 5.0 + 32.0


def f_to_c(value: NumberLike) -> float | None:
    """Degrees Fahrenheit → degrees Celsius."""

    number = to_number(value)
    return None if number is None else (number - 32.0) * 5.0 / 9.0


# ── altimeter ────────────────────────────────────────────────────────────────


class Altimeter(NamedTuple):
    """An altimeter setting with its unit resolved, plus both representations."""

    unit: Literal["inHg", "hPa"]
    """Which unit the raw value was in."""

    in_hg: float
    """The setting in inches of mercury."""

    hpa: float
    """The same setting in hectopascals."""


def normalize_altimeter(value: NumberLike) -> Altimeter | None:
    """Resolve a bare ``altimeter`` number into a unit plus both conversions.

    The live API sends ``parsed.altimeter`` **without a unit** — a US report
    yields ``29.92`` (inHg) and a European one ``1013`` (hPa), and nothing in the
    payload says which. Magnitude is the only signal:

    * ``< 40`` → inches of mercury,
    * ``>= 500`` → hectopascals,
    * anything in between → ``None``. The gap is deliberately wide: guessing in
      the ambiguous range would silently produce a 30x wrong pressure.

    Non-positive and non-numeric inputs are ``None``.
    """

    number = to_number(value)
    if number is None or number <= 0:
        return None
    if number < _ALTIMETER_INHG_MAX:
        return Altimeter(unit="inHg", in_hg=number, hpa=number * HPA_PER_INHG)
    if number >= _ALTIMETER_HPA_MIN:
        return Altimeter(unit="hPa", in_hg=number / HPA_PER_INHG, hpa=number)
    return None


# ── humidity ─────────────────────────────────────────────────────────────────


def humidity_to_percent(value: NumberLike) -> float | None:
    """Relative humidity → percent (``0.62`` → ``62.0``).

    ``MetarParsed.relative_humidity`` is a **fraction**, which reads as "0.62 %"
    if displayed naively. Values above ``1.5`` are assumed to be percentages
    already and are returned unchanged, so the function is safe to apply twice.
    """

    number = to_number(value)
    if number is None:
        return None
    if number > _HUMIDITY_FRACTION_MAX:
        return number
    return number * 100.0


# ── durations ────────────────────────────────────────────────────────────────

_DURATION_PART_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>hours|hour|hrs|hr|h|minutes|minute|mins|min|m|seconds|second|secs|sec|s)"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)
_CLOCK_RE = re.compile(r"^(?P<hours>\d{1,3}):(?P<minutes>[0-5]\d)(?::(?P<seconds>[0-5]\d))?$")


def parse_duration_minutes(text: NumberLike) -> float | None:
    """Parse an English duration string into **minutes**.

    ``ml.flight_time`` reports ``estimated_hours_display`` as prose
    (``"7h 23m"``), and delay endpoints print ``"45 min"`` or ``"2 hours"``.
    Accepted shapes: ``"7h 23m"``, ``"45m"``, ``"2h"``, ``"1 h 5 min"``,
    ``"1h30m15s"``, ``"7:23"`` (h:mm) and ISO-ish ``"PT7H23M"``. A bare number is
    taken to be minutes already. Anything else → ``None``.
    """

    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return to_number(text)
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None

    clock = _CLOCK_RE.match(raw)
    if clock is not None:
        minutes = float(clock["hours"]) * 60.0 + float(clock["minutes"])
        if clock["seconds"] is not None:
            minutes += float(clock["seconds"]) / 60.0
        return minutes

    total: float | None = None
    for match in _DURATION_PART_RE.finditer(raw):
        amount = float(match["value"])
        unit = match["unit"].lower()
        if unit.startswith("h"):
            amount *= 60.0
        elif unit.startswith("s"):
            amount /= 60.0
        total = amount if total is None else total + amount
    if total is not None:
        return total

    return to_number(raw)


def parse_duration(text: NumberLike) -> timedelta | None:
    """Same as :func:`parse_duration_minutes` but returns a :class:`timedelta`.

    Python only — TypeScript has no ``timedelta`` and exposes the minutes form.
    """

    minutes = parse_duration_minutes(text)
    return None if minutes is None else timedelta(minutes=minutes)


# ── visibility ───────────────────────────────────────────────────────────────


class Visibility(NamedTuple):
    """A decoded visibility group.

    Not to be confused with :class:`skylink_api.models.weather.Visibility`, which
    is the raw ``{value, repr}`` block this parser consumes.
    """

    meters: float | None
    """Visibility in metres."""

    statute_miles: float | None
    """The same visibility in statute miles."""

    at_least: bool
    """``True`` for a ``P`` group (``P6SM`` = "6 statute miles or more") and for
    the ``9999`` metre group. The reported figure is then a floor, not a
    measurement."""


_FOUR_DIGIT_RE = re.compile(r"^\d{4}$")
_MIXED_FRACTION_RE = re.compile(r"^(?:(?P<whole>\d+)\s+)?(?P<num>\d+)\s*/\s*(?P<den>\d+)$")


def _parse_fraction(text: str) -> float | None:
    """``"1 1/2"`` → 1.5, ``"1/4"`` → 0.25, ``"10"`` → 10.0."""

    match = _MIXED_FRACTION_RE.match(text)
    if match is not None:
        denominator = float(match["den"])
        if denominator == 0:
            return None
        whole = float(match["whole"]) if match["whole"] else 0.0
        return whole + float(match["num"]) / denominator
    return to_number(text)


def _visibility_from_number(
    number: float,
    *,
    unit: Literal["sm", "m", "km"] | None,
    at_least: bool,
) -> Visibility | None:
    if number < 0:
        return None
    if unit == "sm":
        meters = number * METERS_PER_STATUTE_MILE
        statute_miles = number
    elif unit == "km":
        meters = number * 1000.0
        statute_miles = meters / METERS_PER_STATUTE_MILE
    elif unit == "m":
        meters = number
        statute_miles = number / METERS_PER_STATUTE_MILE
    elif number >= _VISIBILITY_METERS_MIN:
        # No unit anywhere: a bare "10" is statute miles, a bare "9999" is metres.
        meters = number
        statute_miles = number / METERS_PER_STATUTE_MILE
    else:
        meters = number * METERS_PER_STATUTE_MILE
        statute_miles = number
    if meters == _VISIBILITY_UNLIMITED_METERS:
        at_least = True
    return Visibility(meters=meters, statute_miles=statute_miles, at_least=at_least)


def _visibility_from_text(text: str) -> Visibility | None:
    body = text.strip().upper()
    if not body:
        return None

    at_least = False
    if body.startswith("P"):  # "plus" — 6 SM or more
        at_least = True
        body = body[1:]
    elif body.startswith("M"):  # "minus" — less than
        body = body[1:]

    unit: Literal["sm", "m", "km"] | None = None
    if body.endswith("SM"):
        unit = "sm"
        body = body[:-2]
    elif body.endswith("KM"):
        unit = "km"
        body = body[:-2]
    elif body.endswith("M"):
        unit = "m"
        body = body[:-1]
    body = body.strip()

    if unit is None and _FOUR_DIGIT_RE.match(body):
        unit = "m"

    number = _parse_fraction(body)
    if number is None:
        return None
    return _visibility_from_number(number, unit=unit, at_least=at_least)


def _visibility_unit_hint(text: object) -> Literal["sm", "m", "km"] | None:
    if not isinstance(text, str):
        return None
    token = text.strip().upper().lstrip("PM")
    if token.endswith("SM"):
        return "sm"
    if token.endswith("KM"):
        return "km"
    if token.endswith("M") or _FOUR_DIGIT_RE.match(token):
        return "m"
    return None


def parse_visibility(value: object) -> Visibility | None:
    """Decode a visibility group into metres, statute miles and an "or more" flag.

    Accepts a number, a raw token (``"10SM"``, ``"P6SM"``, ``"M1/4SM"``,
    ``"9999"``) or the ``{value, repr}`` block the API nests inside
    ``parsed.visibility`` — including its worst case, ``{"value": None,
    "repr": "P6"}``, where the decoder gave up and only the raw token survives.
    That one is ``P6SM``: six statute miles **or more**, hence
    ``at_least=True``.

    ``M`` (``M1/4SM``) means *less than* and leaves ``at_least`` false — the
    figure is then a ceiling rather than a floor, which is the safe reading.

    A bare number without any unit hint is metres from ``100`` upwards and
    statute miles below that (a US report never says "150 SM", a European one
    never says "8 m").
    """

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _visibility_from_number(float(value), unit=None, at_least=False)
    if isinstance(value, str):
        return _visibility_from_text(value)

    raw = _attr(value, "value")
    token = _attr(value, "repr")
    number = to_number(raw)
    if number is not None:
        hint = _visibility_unit_hint(token)
        at_least = isinstance(token, str) and token.strip().upper().startswith("P")
        return _visibility_from_number(number, unit=hint, at_least=at_least)
    if isinstance(token, str):
        return _visibility_from_text(token)
    return None
