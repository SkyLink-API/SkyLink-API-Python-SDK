"""Derived weather values the API does not compute for you.

Flight category is the obvious one: colouring airports green/blue/red/magenta is
the single most requested thing to do with a METAR, and the backend does not send
it (``parsed.flight_rules`` is present on *some* reports and absent on others).
:func:`flight_category` computes it from the decoded fields — it never touches
the raw METAR text, because the backend already did that work and re-parsing it
would be a second, worse decoder.

Everything here takes either a METAR envelope (``MetarWithParsed``), a decoded
block (``MetarParsed``), a single TAF period (``TafPeriod``) or a plain dict.
**``TafPeriod`` has no ``temperature`` and no ``altimeter``** — the functions are
written against the fields that all of those shapes share and treat the rest as
optional.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Literal, NamedTuple

from .units import NumberLike, _attr, parse_visibility, to_number

__all__ = [
    "FlightCategory",
    "WindComponents",
    "ceiling_ft",
    "flight_category",
    "is_stale",
    "metar_age",
    "wind_components",
]

#: The four US flight categories, worst to best.
FlightCategory = Literal["VFR", "MVFR", "IFR", "LIFR"]

#: Cloud amounts that constitute a ceiling. ``VV`` is a vertical visibility
#: (indefinite ceiling) and ``OVX`` its obscured variant.
_CEILING_TYPES = frozenset({"BKN", "OVC", "VV", "OVX"})

#: Cloud bases are encoded in hundreds of feet (``024`` → 2 400 ft). A "base"
#: this large can only be a source that already converted to feet.
_CLOUD_BASE_FEET_THRESHOLD = 1000.0

_CLOUD_GROUP_RE = re.compile(r"^(?P<type>[A-Z]{2,3})(?P<base>\d{3})")
#: METAR day-hour-minute observation group, e.g. ``271851Z``.
_DAY_HOUR_RE = re.compile(r"^(?P<day>\d{2})(?P<hour>\d{2})(?P<minute>\d{2})Z$")

#: Category thresholds — statute miles of visibility and feet of ceiling.
_LIFR_VISIBILITY_SM = 1.0
_LIFR_CEILING_FT = 500
_IFR_VISIBILITY_SM = 3.0
_IFR_CEILING_FT = 1000
_MVFR_VISIBILITY_SM = 5.0
_MVFR_CEILING_FT = 3000

#: A METAR is issued hourly; 90 minutes without a new one means the station (or
#: the scraper) is down.
_DEFAULT_MAX_AGE = timedelta(minutes=90)


def _decoded(source: object) -> object:
    """Unwrap a ``{... parsed: {...}}`` envelope; pass anything else through."""

    parsed = _attr(source, "parsed")
    return parsed if parsed is not None else source


def _cloud_base_ft(layer: object) -> float | None:
    base = to_number(_attr(layer, "base"))
    if base is None:
        token = _attr(layer, "repr")
        if isinstance(token, str):
            match = _CLOUD_GROUP_RE.match(token.strip().upper())
            if match is not None:
                base = float(match["base"])
    if base is None or base < 0:
        return None
    # Hundreds of feet as encoded, unless the value is already too big to be one.
    return base if base >= _CLOUD_BASE_FEET_THRESHOLD else base * 100.0


def ceiling_ft(source: object) -> int | None:
    """Lowest broken/overcast/vertical-visibility layer, in **feet AGL**.

    ``None`` when the sky is clear, only scattered/few layers were reported, or
    no cloud group was decoded — all three are genuinely "no ceiling", which is
    why they collapse into one answer.

    Cloud bases come encoded in hundreds of feet (``base: 24`` is 2 400 ft) and
    are converted here; a base already given in feet (≥ 1 000) is left alone.
    Accepts a METAR envelope, a decoded block, a TAF period or a dict.
    """

    decoded = _decoded(source)
    layers = _attr(decoded, "clouds", "cloud_layers", "sky_conditions")
    if not isinstance(layers, Iterable) or isinstance(layers, (str, bytes)):
        return None

    lowest: float | None = None
    for layer in layers:
        amount = _attr(layer, "type", "cover", "amount")
        if not isinstance(amount, str) or amount.strip().upper() not in _CEILING_TYPES:
            continue
        base = _cloud_base_ft(layer)
        if base is None:
            continue
        if lowest is None or base < lowest:
            lowest = base
    return None if lowest is None else round(lowest)


def flight_category(source: object) -> FlightCategory | None:
    """Classify conditions as ``VFR``/``MVFR``/``IFR``/``LIFR``.

    Standard US thresholds, worst of the two criteria wins:

    ==========  =========================  ====================
    Category    Visibility (statute miles) Ceiling (feet AGL)
    ==========  =========================  ====================
    ``LIFR``    < 1                        < 500
    ``IFR``     < 3                        < 1 000
    ``MVFR``    ≤ 5                        ≤ 3 000
    ``VFR``     > 5                        > 3 000
    ==========  =========================  ====================

    Works off the **decoded** fields only (``visibility``, ``clouds``) — the raw
    METAR string is never parsed here. ``P6SM`` (which arrives as
    ``{"value": null, "repr": "P6"}``) is correctly read as 6 statute miles or
    more. Returns ``None`` when neither a visibility nor a ceiling could be read,
    since "no data" is not the same as "VFR".
    """

    decoded = _decoded(source)
    visibility = parse_visibility(_attr(decoded, "visibility"))
    statute_miles = visibility.statute_miles if visibility is not None else None
    ceiling = ceiling_ft(decoded)

    if statute_miles is None and ceiling is None:
        return None
    if (statute_miles is not None and statute_miles < _LIFR_VISIBILITY_SM) or (
        ceiling is not None and ceiling < _LIFR_CEILING_FT
    ):
        return "LIFR"
    if (statute_miles is not None and statute_miles < _IFR_VISIBILITY_SM) or (
        ceiling is not None and ceiling < _IFR_CEILING_FT
    ):
        return "IFR"
    if (statute_miles is not None and statute_miles <= _MVFR_VISIBILITY_SM) or (
        ceiling is not None and ceiling <= _MVFR_CEILING_FT
    ):
        return "MVFR"
    return "VFR"


def _parse_report_time(value: object, *, now: datetime) -> datetime | None:
    """Parse an observation time — ISO-8601 or the raw ``271851Z`` day-hour group.

    ``MetarParsed.time`` is whichever of the two the decoder managed to produce,
    which is why the SDK keeps it a string. The day-hour group carries no month,
    so it is resolved against ``now``, stepping back a month when that would put
    the observation in the future.
    """

    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        match = _DAY_HOUR_RE.match(text.upper())
        if match is not None:
            day, hour, minute = (int(match[part]) for part in ("day", "hour", "minute"))
            reference = now.astimezone(timezone.utc)
            for month_offset in (0, -1):
                year, month = reference.year, reference.month + month_offset
                if month < 1:
                    year, month = year - 1, 12
                try:
                    candidate = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
                except ValueError:
                    continue
                if candidate <= reference + timedelta(hours=1):
                    return candidate
            return None
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def metar_age(metar: object, *, now: datetime | None = None) -> timedelta | None:
    """How long ago the report was observed. ``None`` when no time could be read.

    Looks at the decoded observation time first (``parsed.time``, or
    ``observed_time``/``observation_time`` on dict-shaped input) and falls back
    to the envelope ``timestamp``, which is when the API *fetched* the report —
    close enough for staleness, but not the observation instant.

    ``now`` defaults to the current UTC time; naive timestamps are read as UTC.
    A negative result means the station is running ahead of you, not a bug.
    """

    reference = now if now is not None else datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    decoded = _attr(metar, "parsed")
    candidates = (
        _attr(decoded, "time", "observed_time", "observation_time"),
        _attr(metar, "observed_time", "observation_time", "time"),
        _attr(metar, "timestamp"),
    )
    for candidate in candidates:
        observed = _parse_report_time(candidate, now=reference)
        if observed is not None:
            return reference - observed
    return None


def is_stale(
    metar: object,
    *,
    max_age: timedelta = _DEFAULT_MAX_AGE,
    now: datetime | None = None,
) -> bool:
    """Whether the report is older than ``max_age`` (default 90 minutes).

    METARs are issued hourly (SPECIs in between), so anything past ~90 minutes
    means the station or the upstream scraper stopped reporting — worth showing
    differently on a dashboard.

    A report whose time cannot be read counts as **stale**: unknown age is not a
    reason to trust the data.
    """

    age = metar_age(metar, now=now)
    if age is None:
        return True
    return age > max_age


class WindComponents(NamedTuple):
    """Wind resolved into runway-relative components, knots."""

    headwind_kt: float
    """Along the runway; **negative means a tailwind**."""

    crosswind_kt: float
    """Magnitude across the runway — always ≥ 0, see :attr:`from_right`."""

    from_right: bool
    """``True`` when the crosswind blows from the right of the runway heading."""


def wind_components(
    runway_heading_deg: NumberLike,
    wind_direction_deg: NumberLike,
    wind_speed_kt: NumberLike,
) -> WindComponents | None:
    """Split a wind into head/tail and crosswind components for one runway.

    Pair it with ``Runway.le_heading_deg_t`` / ``he_heading_deg_t`` from an
    enriched airport to answer "can I land on 27L". All three arguments accept
    strings, because the enriched-airport payload is forwarded from CSV and its
    numeric columns arrive as strings about as often as numbers.

    Headings are degrees **true** (both the runway headings and the METAR wind
    direction are), speed is knots.

    Returns ``None`` when any of the three cannot be read as a number. That is
    not an error case but the normal one: a variable wind is reported as
    ``direction: null`` with ``repr: "VRB"``, and a runway whose true heading is
    missing from the CSV has ``None`` there. Zeros would read as "calm", so the
    absence is passed on instead::

        components = wind_components(runway.le_heading_deg_t, wind.direction, wind.speed)
        if components is None:
            print("variable or unknown wind")
    """

    heading = to_number(runway_heading_deg)
    direction = to_number(wind_direction_deg)
    speed = to_number(wind_speed_kt)
    if heading is None or direction is None or speed is None:
        return None

    angle = math.radians(direction - heading)
    return WindComponents(
        headwind_kt=speed * math.cos(angle),
        crosswind_kt=abs(speed * math.sin(angle)),
        from_right=math.sin(angle) > 0,
    )
