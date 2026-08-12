"""Models for the ``/airports/*`` endpoints.

Four routes, three shapes:

* ``GET /airports/search`` → a single **enriched** airport
  (:class:`EnrichedAirport`) — the OurAirports row plus ``runways[]``,
  ``frequencies[]``, ``country{}``, ``region{}``, ``navaids[]`` and the two
  ``search_*`` echo fields. No envelope.
* ``GET /airports/search/location`` and ``/ip`` → an envelope around
  :class:`AirportWithDistance`.
* ``GET /airports/search/text`` → an envelope around
  :class:`AirportWithRelevance`.

All three airport variants share :class:`Airport`, so a nearby result and a
text-search hit expose the same core attributes.

**Stringly typed numbers.** The enriched payload is forwarded from the upstream
CSVs with no normalisation, and the API's own documented example shows
``frequency_mhz: "119.1"``, ``frequency_khz: "115900"``, ``lighted: "1"`` and
``closed: "0"`` as *strings* while live rows can just as well be numbers. Those
fields are therefore typed ``str | Number`` and handed over exactly as received —
converting them here would be guesswork. Cast at the call site::

    mhz = float(freq.frequency_mhz) if freq.frequency_mhz is not None else None

``scheduled_service`` is the same story with ``"yes"``/``"no"`` instead of a bool.
"""

from __future__ import annotations

from pydantic import Field

from ._base import SkyLinkModel
from .common import Number
from .geo import Country, Region
from .navaids import Navaid

__all__ = [
    "Airport",
    "AirportFrequency",
    "AirportSearchLocation",
    "AirportWithDistance",
    "AirportWithRelevance",
    "AirportsByIPResponse",
    "AirportsByLocationResponse",
    "AirportsTextSearchResponse",
    "EnrichedAirport",
    "IPLocation",
    "Runway",
]


# ── the airport record and its variants ──────────────────────────────────────


class Airport(SkyLinkModel):
    """Fields every airport payload in the API carries.

    Which of the optional ones are actually populated depends on the route: the
    text search drops ``elevation_ft``/``iso_region``, the location search keeps
    them, and :class:`EnrichedAirport` adds the rest of the CSV row.
    """

    id: int | None = None
    """OurAirports row id. ``None`` on text-search hits built from the search
    index rather than the dataframe."""

    ident: str | None = None
    """Primary identifier — the ICAO code for most airports, an OurAirports
    pseudo-code (``"US-0123"``) for small fields that have none."""

    type: str | None = None
    """One of the seven :data:`~skylink_api.resources.airports.AirportType`
    values. Kept a plain ``str``: response fields never narrow to a ``Literal``."""

    name: str | None = None
    latitude_deg: float | None = None
    longitude_deg: float | None = None
    elevation_ft: Number | None = None
    """Feet above mean sea level."""

    municipality: str | None = None
    """City the airport serves; not necessarily where it is."""

    iso_country: str | None = None
    """ISO 3166-1 alpha-2 (``"US"``)."""

    iso_region: str | None = None
    """ISO 3166-2 (``"US-NY"``)."""

    iata_code: str | None = None
    """3-letter IATA code, ``None`` for the majority of airports."""


class AirportWithDistance(Airport):
    """A nearby-search hit — ``/airports/search/location`` and ``/ip``."""

    distance_km: float | None = None
    """Great-circle distance from the search point, **kilometres** (rounded to
    2 decimals). Results are sorted by it, nearest first."""


class AirportWithRelevance(Airport):
    """A text-search hit — ``/airports/search/text``."""

    relevance_score: int | None = None
    """Ranking score; higher is better. Exact code matches score highest, with a
    bonus for larger airports. The scale is internal and may change."""


# ── enriched search (GET /airports/search) ───────────────────────────────────


class Runway(SkyLinkModel):
    """One runway of an airport, straight from the OurAirports runways CSV.

    A runway is described by its two ends, ``le_*`` (low end, e.g. 04L) and
    ``he_*`` (high end, 22R).
    """

    id: int | None = None
    airport_ref: int | None = None
    """OurAirports id of the owning airport (matches ``Airport.id``)."""

    airport_ident: str | None = None
    length_ft: Number | None = None
    width_ft: Number | None = None
    surface: str | None = None
    """Free-text surface code (``"ASP"``, ``"CON"``, ``"GRE"``, ``"ASPH-G"``) —
    the dataset never settled on a controlled vocabulary."""

    lighted: str | Number | None = None
    """``"1"``/``"0"`` — a string in the documented example, sometimes an int on
    the wire. Compare with ``str(runway.lighted) == "1"``."""

    closed: str | Number | None = None
    """``"1"``/``"0"``, same caveat as :attr:`lighted`."""

    le_ident: str | None = None
    le_latitude_deg: float | None = None
    le_longitude_deg: float | None = None
    le_elevation_ft: Number | None = None
    le_heading_deg_t: float | None = Field(default=None, alias="le_heading_degT")
    """Magnetic-north-corrected true heading of the low end. Wire key is
    ``le_heading_degT``."""

    le_displaced_threshold_ft: Number | None = None
    he_ident: str | None = None
    he_latitude_deg: float | None = None
    he_longitude_deg: float | None = None
    he_elevation_ft: Number | None = None
    he_heading_deg_t: float | None = Field(default=None, alias="he_heading_degT")
    """True heading of the high end. Wire key is ``he_heading_degT``."""

    he_displaced_threshold_ft: Number | None = None


class AirportFrequency(SkyLinkModel):
    """A communication frequency assigned to the airport."""

    id: int | None = None
    airport_ref: int | None = None
    type: str | None = None
    """``TWR``, ``GND``, ``ATIS``, ``APP``, ``CTAF``... uncontrolled vocabulary."""

    description: str | None = None
    frequency_mhz: str | Number | None = None
    """Megahertz — ``"119.1"`` as a string in the documented example, a float on
    live rows. Never coerced; ``float(...)`` it yourself when you need a number."""


class EnrichedAirport(Airport):
    """``GET /airports/search`` — one airport with everything attached.

    On top of :class:`Airport` this carries the remaining CSV columns plus five
    joined blocks and the two ``search_*`` echo fields. The response is the
    airport object itself; there is no envelope and no ``total``.
    """

    continent: str | None = None
    """``AF``, ``AN``, ``AS``, ``EU``, ``NA``, ``OC`` or ``SA``."""

    scheduled_service: str | None = None
    """``"yes"`` or ``"no"`` — a **string**, not a bool."""

    icao_code: str | None = None
    """Official ICAO code when the dataset distinguishes it from ``ident``."""

    gps_code: str | None = None
    local_code: str | None = None
    """National identifier (US FAA ``"JFK"``, and so on)."""

    home_link: str | None = None
    wikipedia_link: str | None = None
    keywords: str | None = None
    """Comma separated alternate names and city aliases."""

    runways: list[Runway] = Field(default_factory=list)
    """Every runway on record. Empty for heliports and most small fields."""

    frequencies: list[AirportFrequency] = Field(default_factory=list)
    navaids: list[Navaid] = Field(default_factory=list)
    """Navaids whose ``associated_airport`` is this airport's ``ident``. Served
    from the raw CSV, so frequencies here are strings."""

    country: Country | None = None
    """Joined from the countries dataset via ``iso_country``; ``None`` when the
    code is missing or unknown."""

    region: Region | None = None
    """Joined from the regions dataset via ``iso_region``."""

    search_code: str | None = None
    """The code that was looked up, upper-cased and stripped."""

    search_type: str | None = None
    """``"ICAO"`` or ``"IATA"`` — which parameter the caller supplied."""


# ── search envelopes ─────────────────────────────────────────────────────────


class AirportSearchLocation(SkyLinkModel):
    """The ``search_location`` block echoed by ``/airports/search/location``.

    Typed rather than left a bare ``dict``: the backend builds it from a literal
    with these four keys. Unknown additions still survive (``extra="allow"``).
    """

    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None
    """The radius that was applied, in kilometres."""

    type_filter: str | None = None
    """The ``type`` filter that was applied, or ``None`` when unfiltered."""


class AirportsByLocationResponse(SkyLinkModel):
    """Envelope of ``GET /airports/search/location``."""

    search_location: AirportSearchLocation | None = None
    airports: list[AirportWithDistance] = Field(default_factory=list)
    """Sorted by ``distance_km``, nearest first, truncated to ``limit``."""

    airports_found: int = 0
    """Length of ``airports`` **after** the limit — not the pre-limit match count."""


class IPLocation(SkyLinkModel):
    """Geolocation of the IP address, from the bundled DB-IP database.

    City-level accuracy at best; every field is nullable because the database
    resolves some ranges to a country only.
    """

    latitude: float | None = None
    longitude: float | None = None
    city: str | None = None
    region: str | None = None
    """Region *name* (``"Kansas"``), not an ISO code."""

    country: str | None = None
    """Country name (``"United States"``)."""

    country_code: str | None = None
    postal: str | None = None
    timezone: str | None = None
    """IANA zone (``"America/Chicago"``)."""

    ip: str | None = None


class AirportsByIPResponse(SkyLinkModel):
    """Envelope of ``GET /airports/search/ip``.

    A geolocation failure is **not** an HTTP error: the route answers ``200``
    with ``error`` set, ``location=None`` and an empty ``airports`` list. Check
    ``error`` before trusting the results.
    """

    ip_address: str | None = None
    """The IP that was geolocated — the caller's own when ``ip`` was omitted."""

    location: IPLocation | None = None
    """``None`` when geolocation failed (see ``error``)."""

    airports: list[AirportWithDistance] = Field(default_factory=list)
    search_radius_km: int | None = None
    """The radius that was applied, truncated to a whole kilometre by the API."""

    airports_found: int = 0
    error: str | None = None
    """Geolocation failure message, delivered inside a ``200`` response."""


class AirportsTextSearchResponse(SkyLinkModel):
    """Envelope of ``GET /airports/search/text``."""

    query: str | None = None
    """The query string, echoed back."""

    airports: list[AirportWithRelevance] = Field(default_factory=list)
    """Sorted by ``relevance_score``, best first."""

    airports_found: int = 0
