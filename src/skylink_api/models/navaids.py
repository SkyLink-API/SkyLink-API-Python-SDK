"""Models for ``GET /navaids``.

Two things make this namespace unusual:

* ``usageType`` is the **only camelCase key in the whole API**. It is exposed as
  ``usage_type`` with an explicit alias so the Python side stays snake_case.
* ``filters_applied`` is a genuine dynamic map — the backend adds one key per
  filter it actually applied (and normalises the value: ``airport``, ``type``
  and ``country`` come back upper-cased, ``bbox`` as the joined string), so it
  is typed ``dict[str, Any]`` rather than a model.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ._base import SkyLinkModel
from .common import Number

__all__ = ["Navaid", "NavaidsResponse"]


class Navaid(SkyLinkModel):
    """A navigation aid (VOR, NDB, ILS, DME, TACAN, ...).

    Reused verbatim for the ``navaids[]`` block of an enriched airport
    (``GET /airports/search``), which is served straight from the OurAirports
    CSV and therefore carries the extra DME columns and ``filename`` declared
    below. Frequencies arrive there as **strings** while ``GET /navaids`` sends
    numbers — hence the ``str | Number`` unions.
    """

    id: int | None = None
    ident: str | None = None
    """Navaid identifier (``"JFK"``); not unique worldwide."""

    name: str | None = None
    type: str | None = None
    """``VOR``, ``NDB``, ``ILS``, ``DME``, ``TACAN``, ``VOR-DME``, ``VORTAC``..."""

    frequency_khz: str | Number | None = None
    """Kilohertz. A number from ``GET /navaids``, a string (``"115900"``) when
    embedded in an airport payload — kept as sent, never coerced."""

    latitude_deg: float | None = None
    longitude_deg: float | None = None
    elevation_ft: float | None = None
    iso_country: str | None = None
    dme_frequency_khz: str | Number | None = None
    dme_channel: str | None = None
    slaved_variation_deg: float | None = None
    magnetic_variation_deg: float | None = None
    usage_type: str | None = Field(default=None, alias="usageType")
    """``HI``, ``LO``, ``BOTH``, ``TERMINAL``, ``RNAV``.

    The single camelCase field in the API. Construct with either name
    (``populate_by_name=True``); the wire key is always ``usageType``.
    """

    power: str | None = None
    """``HIGH``, ``MEDIUM``, ``LOW``, ``UNKNOWN``."""

    associated_airport: str | None = None
    """ICAO ident of the airport this navaid serves, when there is one."""

    # ── OurAirports CSV columns, present on the enriched-airport form ─────────
    dme_latitude_deg: float | None = None
    dme_longitude_deg: float | None = None
    """Position of the DME transmitter when it is not co-located with the
    navaid. Only sent inside ``GET /airports/search``."""

    dme_elevation_ft: float | str | None = None
    """DME antenna elevation. Arrives as a string in the airport payload."""

    filename: str | None = None
    """OurAirports slug for the record (``"JFK-VOR-DME-JFK"``); useful as a
    stable key, not shown by ``GET /navaids``."""


class NavaidsResponse(SkyLinkModel):
    """Envelope of ``GET /navaids``."""

    navaids: list[Navaid] = Field(default_factory=list)
    total: int = 0
    """Matches **before** ``limit`` was applied, so it can exceed
    ``len(navaids)``. There is no offset parameter — narrow the filters instead."""

    filters_applied: dict[str, Any] = Field(default_factory=dict)
    """The filters the backend actually used, normalised. Only keys for the
    filters that were supplied are present."""
