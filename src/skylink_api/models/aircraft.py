"""Models for the ``/aircraft/*`` endpoints — registry lookup, performance, stats.

The one thing to internalise about this namespace: **lookups never 404**. A
registration or hex address that is not in the database comes back as a plain
``200`` with ``found=False`` and ``aircraft=None``, so the SDK models it as a
sentinel rather than raising::

    result = sky.aircraft.by_registration("ZZ-ZZZ")
    if result.found and result.aircraft is not None:
        print(result.aircraft.type_name)

``year_built`` is a **string**, not an int. The registry stores it verbatim and
values such as ``"2010"`` sit next to blanks and the occasional non-numeric
entry, so no coercion happens anywhere in the chain.

:class:`AircraftPerformance` is scraped reference data served from a JSON blob
without a response model — every field there is optional.
"""

from __future__ import annotations

from pydantic import Field

from ._base import SkyLinkModel
from .common import Number

__all__ = [
    "AircraftDatabaseStats",
    "AircraftDetails",
    "AircraftLookup",
    "AircraftPerformance",
    "AircraftPhoto",
]


# ── registry lookup ──────────────────────────────────────────────────────────


class AircraftPhoto(SkyLinkModel):
    """A photo of the airframe, credited to its author.

    Present only when the request asked for photos (the default) and the photo
    provider answered in time.
    """

    image: str | None = None
    """Direct URL of the image file."""

    link: str | None = None
    """URL of the photo's page on the provider's site — use this for attribution
    links rather than ``image``."""

    photographer: str | None = None


class AircraftDetails(SkyLinkModel):
    """Registry record for one airframe.

    Every scalar is optional: rows come from a merged registry dump and sparse
    entries (private owners, recently registered airframes) are common.
    """

    registration: str | None = None
    """Tail number (``"G-STBA"``)."""

    icao24: str | None = None
    """ICAO 24-bit transponder address, hex."""

    icao_type: str | None = None
    """ICAO type designator (``"B77W"``)."""

    type_name: str | None = None
    """Model name as the registry spells it (``"Boeing 777-336(ER)"``)."""

    manufacturer: str | None = None
    manufacturer_and_model: str | None = None
    """Pre-joined ``manufacturer`` + ``type_name``. Added in v3.1."""

    owner_operator: str | None = None
    airline_code: str | None = None
    """Operator code (``"BAW"``). Added in v3.1."""

    is_private_operator: bool | None = None
    """``True`` when the registered operator looks like a private owner rather
    than an airline. Added in v3.1."""

    serial_number: str | None = None
    """Manufacturer serial number (MSN). Added in v3.1."""

    year_built: str | None = None
    """Year of manufacture **as a string** (``"2010"``). The registry value is
    forwarded verbatim, so it is never converted to an ``int``."""

    photos: list[AircraftPhoto] = Field(default_factory=list)
    """Empty when the lookup ran with ``photos=False`` or nothing was found."""


class AircraftLookup(SkyLinkModel):
    """Result of ``GET /aircraft/registration/{reg}`` and ``/aircraft/icao24/{hex}``.

    A miss is **not** an error: the endpoint answers ``200`` with
    ``found=False`` and ``aircraft=None``. Check ``found`` (or just test
    ``aircraft is not None``) instead of catching
    :class:`~skylink_api.NotFoundError`.
    """

    query: str | None = None
    """The identifier that was looked up, upper-cased by the API."""

    found: bool = False
    aircraft: AircraftDetails | None = None
    """``None`` exactly when ``found`` is ``False``."""


class AircraftDatabaseStats(SkyLinkModel):
    """Envelope of ``GET /aircraft/database/stats``.

    Useful as a readiness probe: ``loaded=False`` means the lookup endpoints will
    report ``found=False`` for everything until the dump finishes downloading.
    """

    loaded: bool = False
    total_icao_entries: int = 0
    """Rows indexed by ICAO24 hex address."""

    total_registration_entries: int = 0
    """Rows indexed by registration — slightly fewer than the ICAO index, since
    some airframes have a hex address but no recorded tail number."""

    source_url: str | None = None
    """Where the dump was fetched from. Named ``database_file`` in v3."""


# ── performance ──────────────────────────────────────────────────────────────


class AircraftPerformance(SkyLinkModel):
    """Performance profile of an aircraft *type* (``GET /aircraft/performance/{icao_type}``).

    Reference data keyed by ICAO type designator, not by airframe — ``B77W``
    describes every 777-300ER, and ``max_passengers`` is a typical certified
    figure, not a specific operator's cabin layout.

    **All fields are optional.** The dataset is scraped and coverage varies by
    type: rarer designators arrive with little more than a ``name``.
    """

    icao_type: str | None = None
    """ICAO type designator, echoed back upper-cased (``"B77W"``)."""

    name: str | None = None
    """Manufacturer and model as the source spells it (``"BOEING 777-300ER"``)."""

    engine_type: str | None = None
    """``"Jet"``, ``"Piston"``, ``"Turboprop/Turboshaft"``, ``"Electric"``..."""

    engine_code: str | None = None
    """ICAO engine code: count + type + placement, e.g. ``"L2J"`` = landplane,
    2 jet engines."""

    wake_category: str | None = None
    """ICAO wake turbulence category: ``"L"``, ``"M"``, ``"H"`` or ``"J"``."""

    cruise_speed_ktas: Number | None = None
    """Knots true airspeed."""

    service_ceiling_ft: Number | None = None
    max_range_nm: Number | None = None
    """Nautical miles."""

    wing_span_m: Number | None = None
    length_m: Number | None = None
    mtow_t: Number | None = None
    """Maximum take-off weight in **metric tonnes**."""

    max_passengers: Number | None = None
