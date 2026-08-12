"""Models for the ``/weather/*`` endpoints.

Naming conventions used here (and copied by every other namespace):

* A single document keeps its plain domain name — :class:`Metar`, :class:`Taf`.
* ``?parsed=true`` returns the same document plus a ``parsed`` block, so it gets
  its own subclass (:class:`MetarWithParsed`, :class:`TafWithParsed`). That way
  ``parsed`` only exists on the static type when it was actually requested.
* Bounding-box endpoints return an envelope (``bbox`` + items + ``total``) and
  keep the ``*Response`` suffix.

Typing policy for enum-ish strings: **request** parameters use ``Literal`` (free
autocomplete, no runtime risk) while **response** fields stay ``str``. The API
scrapes upstream sources, so a value outside the documented set is a real
possibility and must not turn into a validation error.

Opaque times stay strings: ``MetarParsed.time`` is either an ISO timestamp or the
raw day-hour group (``"271851Z"``), and ``valid_time``/``start_time``/``end_time``
are whatever the upstream bulletin printed. Only the envelope ``timestamp``,
which the API generates itself as ISO-8601 with ``Z``, is a real ``datetime``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from ._base import SkyLinkModel
from .common import Number

__all__ = [
    "AirSigmetCoord",
    "AirSigmetMovement",
    "AirSigmetObservation",
    "AirSigmetReport",
    "AirSigmetResponse",
    "CloudLayer",
    "Metar",
    "MetarParsed",
    "MetarWithParsed",
    "PirepReport",
    "PirepsResponse",
    "Taf",
    "TafParsed",
    "TafPeriod",
    "TafWithParsed",
    "Visibility",
    "WeatherReport",
    "Wind",
    "WindLevel",
    "WindsAloftResponse",
    "WindsAloftStation",
    "WxCode",
]


# ── shared decoded groups (METAR and TAF periods use the same shapes) ─────────


class Wind(SkyLinkModel):
    """Decoded wind group."""

    direction: Number | None = None
    """Degrees true; ``None`` for a variable (``VRB``) or missing direction."""

    speed: Number | None = None
    """Knots."""

    gust: Number | None = None
    """Knots; ``None`` when no gust was reported."""

    variable: Any = None
    """Variable-direction range (``180V240``).

    Untyped on purpose: upstream hands over a list of decoded objects and the
    API forwards it as-is, so this is normally a list of ``{repr, value}`` dicts
    but occasionally a bare number or ``None``.
    """


class Visibility(SkyLinkModel):
    """Prevailing visibility, both decoded and as printed in the report."""

    value: Number | None = None
    """Statute miles (US) or metres, matching the source report."""

    repr: str | None = None
    """The token as it appears in the raw report (``"10SM"``, ``"P6SM"``)."""


class CloudLayer(SkyLinkModel):
    """One cloud layer of a sky-condition group."""

    type: str | None = None
    """Layer amount: ``FEW``, ``SCT``, ``BKN``, ``OVC``, ``VV``, ``CLR``/``SKC``."""

    base: Number | None = None
    """Base in hundreds of feet AGL, as encoded (``024`` → ``24``)."""

    repr: str | None = None
    """The raw group (``"FEW024"``)."""


class WxCode(SkyLinkModel):
    """A present-weather code and its expansion."""

    repr: str | None = None
    """The raw code (``"-RA"``, ``"BR"``)."""

    value: str | None = None
    """Human readable expansion (``"Light Rain"``)."""


# ── METAR ────────────────────────────────────────────────────────────────────


class MetarParsed(SkyLinkModel):
    """Decoded METAR fields returned when ``parsed=True``.

    Every leaf is nullable — the decoder is best effort and simply omits what it
    could not make sense of.
    """

    wind: Wind | None = None
    visibility: Visibility | None = None
    clouds: list[CloudLayer] = Field(default_factory=list)
    temperature: Number | None = None
    """Degrees Celsius."""

    dewpoint: Number | None = None
    """Degrees Celsius."""

    altimeter: Number | None = None
    """Inches of mercury (US) or hectopascals, matching the source report."""

    flight_rules: str | None = None
    """``VFR``, ``MVFR``, ``IFR`` or ``LIFR``."""

    wx_codes: list[WxCode] = Field(default_factory=list)
    remarks: str | None = None
    """Everything after the ``RMK`` group, verbatim."""

    time: str | None = None
    """Observation time — ISO-8601 when the decoder resolved a date, otherwise
    the raw day-hour group (``"271851Z"``). Left as a string for that reason."""

    density_altitude: Number | None = None
    """Feet."""

    pressure_altitude: Number | None = None
    """Feet."""

    relative_humidity: Number | None = None
    """Fraction in ``[0, 1]``, not percent."""


class WeatherReport(SkyLinkModel):
    """Fields common to the METAR and TAF envelopes."""

    raw: str | None = None
    """The report exactly as issued."""

    icao: str | None = None
    """The requested station, upper-cased by the API."""

    airport_name: str | None = None
    """Resolved from the airport database, not from the report."""

    timestamp: datetime | None = None
    """When the API fetched the report (ISO-8601 UTC). Not the observation time —
    that lives in ``parsed.time``."""


class Metar(WeatherReport):
    """A METAR observation (``GET /weather/metar/{icao}``)."""


class MetarWithParsed(Metar):
    """A METAR observation fetched with ``parsed=True``."""

    parsed: MetarParsed | None = None
    """Decoded fields, or ``None`` when the API could not decode the report.

    The key is always present in this variant but its value is genuinely
    nullable — the backend swallows decoder failures and sends ``null``.
    """


# ── TAF ──────────────────────────────────────────────────────────────────────


class TafPeriod(SkyLinkModel):
    """One forecast period (``FM``/``TEMPO``/``BECMG`` group) of a TAF."""

    type: str | None = None
    """Group type: ``FROM``, ``TEMPO``, ``BECMG``, ``PROB``..."""

    start_time: str | None = None
    """ISO-8601 when resolvable, otherwise the raw group. Opaque string."""

    end_time: str | None = None
    """ISO-8601 when resolvable, otherwise the raw group. Opaque string."""

    wind: Wind | None = None
    visibility: Visibility | None = None
    clouds: list[CloudLayer] = Field(default_factory=list)
    wx_codes: list[WxCode] = Field(default_factory=list)
    flight_rules: str | None = None
    probability: Any = None
    """``PROB30``/``PROB40`` probability. Untyped: forwarded from the decoder
    unchanged, so it is a number or a ``{repr, value}`` dict depending on source."""

    turbulence: list[str] = Field(default_factory=list)
    """Stringified turbulence groups."""

    icing: list[str] = Field(default_factory=list)
    """Stringified icing groups."""


class TafParsed(SkyLinkModel):
    """Decoded TAF fields returned when ``parsed=True``."""

    start_time: str | None = None
    """Start of the forecast validity window. Opaque string."""

    end_time: str | None = None
    """End of the forecast validity window. Opaque string."""

    forecast: list[TafPeriod] = Field(default_factory=list)


class Taf(WeatherReport):
    """A terminal aerodrome forecast (``GET /weather/taf/{icao}``)."""


class TafWithParsed(Taf):
    """A TAF fetched with ``parsed=True``."""

    parsed: TafParsed | None = None
    """Decoded forecast, or ``None`` when the API could not decode the report."""


# ── winds aloft ──────────────────────────────────────────────────────────────


class WindLevel(SkyLinkModel):
    """Forecast wind and temperature at one altitude."""

    altitude_ft: int | None = None
    """Altitude in feet MSL."""

    wind_direction: int | None = None
    """Degrees true (0-360)."""

    wind_speed_kt: int | None = None
    temperature_c: int | None = None
    light_and_variable: bool = False
    """True when the level was encoded as light and variable (< 5 kt)."""

    raw: str | None = None
    """The raw four/six digit group for this level (``"2709"``)."""


class WindsAloftStation(SkyLinkModel):
    """One FB winds reporting station and its levels."""

    station: str | None = None
    """Three letter FB winds identifier (``"JFK"``), not an ICAO code."""

    latitude: float | None = None
    longitude: float | None = None
    winds: list[WindLevel] = Field(default_factory=list)
    raw_text: str | None = None
    """The station's full raw FD bulletin line."""


class WindsAloftResponse(SkyLinkModel):
    """Envelope of ``GET /weather/winds-aloft``. US coverage only (FAA FD product)."""

    bbox: str | None = None
    """The bounding box as echoed back, ``"lat1,lon1,lat2,lon2"``."""

    forecast_hour: int | None = None
    """The forecast period actually used — 6, 12 or 24, after the API snapped
    the requested value."""

    level: str | None = None
    """``"low"`` (3K-39K ft) or ``"high"`` (6K-45K ft)."""

    valid_time: str | None = None
    """Bulletin validity group (``"130000Z"``). Opaque string, not a datetime."""

    stations: list[WindsAloftStation] = Field(default_factory=list)
    total: int = 0


# ── PIREPs ───────────────────────────────────────────────────────────────────


class PirepReport(SkyLinkModel):
    """A single pilot report.

    Everything but ``raw`` is a string, including the numeric-looking fields
    (``altitude``, ``temperature``, ``wind``) — they carry units and encodings
    (``"FL085"``, ``"-12C"``) and are never normalised by the API.
    """

    raw: str | None = None
    report_type: str | None = None
    """``"UA"`` (routine) or ``"UUA"`` (urgent)."""

    location: str | None = None
    """Station identifier, or a bearing/distance from a navaid (``"JFK180015"``)."""

    time: str | None = None
    altitude: str | None = None
    aircraft_type: str | None = None
    sky_conditions: str | None = None
    turbulence: str | None = None
    icing: str | None = None
    temperature: str | None = None
    wind: str | None = None
    remarks: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class PirepsResponse(SkyLinkModel):
    """Envelope of ``GET /weather/pireps``."""

    bbox: str | None = None
    hours: int | None = None
    """The look-back window that was applied."""

    reports: list[PirepReport] = Field(default_factory=list)
    total: int = 0


# ── AIRMET / SIGMET ──────────────────────────────────────────────────────────


class AirSigmetCoord(SkyLinkModel):
    """One vertex of an advisory polygon."""

    lat: float | None = None
    lon: float | None = None


class AirSigmetMovement(SkyLinkModel):
    """Movement of the affected area. Both fields are strings, units included."""

    direction: str | None = None
    speed: str | None = None


class AirSigmetObservation(SkyLinkModel):
    """Conditions block of an advisory.

    The same shape is used for both the observed and the forecast half of a
    report, hence the neutral field names.
    """

    type: str | None = None
    """Phenomenon: ``TURB``, ``ICE``, ``IFR``, ``MTN OBSCN``..."""

    intensity: str | None = None
    """``MOD``, ``SEV``..."""

    floor: str | None = None
    """Lower bound (``"SFC"``, ``"FL180"``). String, not a number."""

    ceiling: str | None = None
    """Upper bound (``"FL450"``). String, not a number."""

    coords: list[AirSigmetCoord] = Field(default_factory=list)
    movement: AirSigmetMovement | None = None


class AirSigmetReport(SkyLinkModel):
    """A single AIRMET, SIGMET or Convective SIGMET advisory."""

    raw: str | None = None
    bulletin_type: str | None = None
    """``"AIRMET"`` or ``"SIGMET"`` as taken from the bulletin header."""

    country: str | None = None
    issuer: str | None = None
    area: str | None = None
    report_type: str | None = None
    """``"AIRMET"``, ``"SIGMET"`` or ``"CONVECTIVE SIGMET"``."""

    start_time: str | None = None
    """Validity start. Opaque string, not a datetime."""

    end_time: str | None = None
    """Validity end. Opaque string, not a datetime."""

    body: str | None = None
    region: str | None = None
    """Affected FIR/region."""

    observation: AirSigmetObservation | None = None
    forecast: AirSigmetObservation | None = None


class AirSigmetResponse(SkyLinkModel):
    """Envelope of ``GET /weather/airsigmet``."""

    bbox: str | None = None
    reports: list[AirSigmetReport] = Field(default_factory=list)
    total: int = 0
    filter_type: str | None = None
    """The ``type`` filter that was applied, echoed back."""
