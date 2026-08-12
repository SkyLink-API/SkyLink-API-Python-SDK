"""Models for ``/charts/*`` — aerodrome chart links, by category.

The one shape to know is :attr:`ChartsResponse.charts`: a **partial** map from
category to charts. The backend drops categories with no charts instead of
sending empty lists, so ``"APP" in response.charts`` is a real question and
``response.charts.get("APP", [])`` is the safe access pattern.

Keys are the five :data:`~skylink_api.resources.charts.ChartCategory` values
(``GEN``, ``GND``, ``SID``, ``STAR``, ``APP``) but are typed ``str``, in line
with the SDK's rule that response enums stay open.

The API never proxies the PDFs — ``Chart.url`` points straight at the national
AIP or FAA host, which may impose its own rate limits or expire the link.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ._base import SkyLinkModel

__all__ = ["Chart", "ChartSource", "ChartSourcesResponse", "ChartsResponse"]


class Chart(SkyLinkModel):
    """A single chart and where to fetch it."""

    name: str | None = None
    """Title as published (``"KJFK - Airport Diagram"``)."""

    url: str | None = None
    """Direct link to the PDF **on the publisher's server** — not proxied by the
    SDK or the API."""

    category: str | None = None
    """``"GEN"``, ``"GND"``, ``"SID"``, ``"STAR"`` or ``"APP"``. Repeats the key
    this chart is filed under in :attr:`ChartsResponse.charts`."""


class ChartsResponse(SkyLinkModel):
    """Envelope of ``GET /charts/{icao}`` and ``GET /charts/{icao}/{category}``."""

    icao_code: str | None = None
    """The requested airport, upper-cased."""

    source: str | None = None
    """Which national scraper answered (``"faa"``, ``"uk"``, ``"france"``...),
    either auto-detected from the ICAO prefix or the ``source`` override."""

    charts: dict[str, list[Chart]] = Field(default_factory=dict)
    """Charts by category — a **partial** map.

    Categories with no charts are omitted entirely rather than sent as empty
    lists, and the single-category endpoint narrows this to at most one key. Use
    ``charts.get("SID", [])`` rather than indexing.
    """

    total_count: int = 0
    """Charts across all categories in *this* response — the single-category
    endpoint reports the filtered count, not the airport's total."""

    fetched_at: datetime | None = None
    """When the scrape ran. Naive UTC (no timezone) on the wire."""


class ChartSource(SkyLinkModel):
    """One national chart provider and the ICAO prefixes it covers."""

    source_id: str | None = None
    """Identifier to pass as the ``source`` override (``"faa"``, ``"japan"``)."""

    name: str | None = None
    """Human-readable provider name."""

    icao_prefixes: list[str] = Field(default_factory=list)
    """Prefixes routed to this source.

    Mostly plain prefixes (``"K"``, ``"EG"``), but the catch-all Russian source
    carries a **descriptive sentinel** rather than a prefix:
    ``"U* (except UA,UC,UG,UM,UT,UZ)"``. Do not parse these blindly.
    """


class ChartSourcesResponse(SkyLinkModel):
    """Envelope of ``GET /charts/sources``."""

    sources: list[ChartSource] = Field(default_factory=list)
    total_count: int = 0
