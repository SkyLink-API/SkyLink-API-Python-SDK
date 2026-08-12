"""Models for the ``/countries/*`` and ``/regions/*`` endpoints.

Both resources come from the OurAirports reference CSVs and follow the same
shape: a ``{items, total}`` envelope for the list route and a **flat** record for
the detail route. The detail response is not wrapped in an envelope and is not
the same declared type as a list element on the backend either, so each gets its
own model here (:class:`Country` vs :class:`CountryDetail`,
:class:`Region` vs :class:`RegionDetail`).

Field-for-field the two forms currently agree; the split exists so that a future
divergence — the backend already declares them as separate pydantic models —
lands on one class instead of silently widening both.
"""

from __future__ import annotations

from pydantic import Field

from ._base import SkyLinkModel

__all__ = [
    "CountriesResponse",
    "Country",
    "CountryDetail",
    "Region",
    "RegionDetail",
    "RegionsResponse",
]


# ── countries ────────────────────────────────────────────────────────────────


class Country(SkyLinkModel):
    """One row of ``GET /countries``.

    The same shape is embedded as the ``country`` block of an enriched airport
    (:class:`~skylink_api.models.airports.EnrichedAirport`).
    """

    id: int | None = None
    """OurAirports row id, not an ISO number."""

    code: str | None = None
    """ISO 3166-1 alpha-2 code (``"US"``)."""

    name: str | None = None
    continent: str | None = None
    """``AF``, ``AN``, ``AS``, ``EU``, ``NA``, ``OC`` or ``SA``. Left as ``str``:
    response fields never narrow to a ``Literal`` in this SDK."""

    wikipedia_link: str | None = None
    keywords: str | None = None
    """Comma separated free text, occasionally empty rather than absent."""


class CountryDetail(SkyLinkModel):
    """``GET /countries/{code}`` — the record itself, **not** wrapped.

    Unlike the list route there is no ``{countries, total}`` envelope: the body
    *is* the country.
    """

    id: int | None = None
    code: str | None = None
    """ISO 3166-1 alpha-2 code; always present in practice."""

    name: str | None = None
    continent: str | None = None
    wikipedia_link: str | None = None
    keywords: str | None = None


class CountriesResponse(SkyLinkModel):
    """Envelope of ``GET /countries``."""

    countries: list[Country] = Field(default_factory=list)
    total: int = 0
    """Number of rows in ``countries`` — the route has no pagination."""


# ── regions ──────────────────────────────────────────────────────────────────


class Region(SkyLinkModel):
    """One row of ``GET /regions`` (a state, province or territory).

    Also embedded as the ``region`` block of an enriched airport.
    """

    id: int | None = None
    code: str | None = None
    """ISO 3166-2 code (``"US-NY"``)."""

    local_code: str | None = None
    """Subdivision part only (``"NY"``)."""

    name: str | None = None
    continent: str | None = None
    iso_country: str | None = None
    """ISO 3166-1 alpha-2 code of the owning country."""

    wikipedia_link: str | None = None
    keywords: str | None = None


class RegionDetail(SkyLinkModel):
    """``GET /regions/{code}`` — the record itself, **not** wrapped."""

    id: int | None = None
    code: str | None = None
    local_code: str | None = None
    name: str | None = None
    continent: str | None = None
    iso_country: str | None = None
    wikipedia_link: str | None = None
    keywords: str | None = None


class RegionsResponse(SkyLinkModel):
    """Envelope of ``GET /regions``."""

    regions: list[Region] = Field(default_factory=list)
    total: int = 0
