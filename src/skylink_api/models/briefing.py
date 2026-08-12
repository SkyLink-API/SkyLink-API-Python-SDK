"""Models for the ``/briefing/*`` endpoints (AI generated pre-flight briefings).

Two response shapes live behind one endpoint, selected by the ``format`` query
parameter:

* ``format=json`` → :class:`FlightBriefing`, the structured briefing.
* ``format=markdown|plain_text|html`` → :class:`FlightBriefingText`, an envelope
  whose ``briefing`` field carries the rendered document as one string.

``GET /briefing/pdf`` has no model at all — it is the only binary endpoint in the
API and comes back as ``bytes``.

The nullable-list trap: :attr:`AirportBriefing.notams` and
:attr:`AirportBriefing.pireps` are ``None`` — not ``[]`` — when the caller
excluded that source (``include_notams=False`` / ``include_pireps=False``, the
latter being the default). An empty list means "the source was consulted and had
nothing"; ``None`` means "the source was never consulted". Both appear in the
same response, so the distinction is load bearing and is preserved here.
"""

from __future__ import annotations

from pydantic import Field

from ._base import SkyLinkModel

__all__ = [
    "AirportBriefing",
    "BriefingNotam",
    "BriefingPirep",
    "BriefingRestriction",
    "BriefingWeather",
    "FlightBriefing",
    "FlightBriefingText",
]


# ── building blocks ──────────────────────────────────────────────────────────


class BriefingWeather(SkyLinkModel):
    """Weather block of one airport's briefing.

    Present only when the request ran with ``include_weather=True``; the raw
    reports are echoed verbatim next to the AI written summary.
    """

    metar_raw: str | None = None
    """The METAR exactly as issued, or ``None`` when the station had none."""

    taf_raw: str | None = None
    """The TAF exactly as issued, or ``None`` when the station issues no TAF."""

    conditions: str | None = None
    """Plain-language summary of the two reports above, written by the model."""


class BriefingNotam(SkyLinkModel):
    """One operationally relevant NOTAM, rewritten in plain language."""

    title: str | None = None
    """Short headline (``"Taxiway B closed"``)."""

    description: str | None = None
    """What it means for the flight, in prose."""

    affected: str | None = None
    """The affected element (``"TWY B"``, ``"RWY 29"``); ``None`` when unclear."""

    notam_id: str | None = None
    """Source NOTAM identifier (``"01/234"``) when the model could attribute it."""


class BriefingPirep(SkyLinkModel):
    """A pilot report near the airport (within 100 nm, last 3 hours)."""

    raw: str | None = None
    """The report as filed."""

    summary: str | None = None
    """Plain-language reading of it."""


class BriefingRestriction(SkyLinkModel):
    """Something that could prevent or significantly alter the flight."""

    icao: str | None = None
    """Which of the two airports this restriction belongs to."""

    description: str | None = None
    affected: str | None = None
    notam_id: str | None = None


class AirportBriefing(SkyLinkModel):
    """Per-airport half of a structured briefing.

    ``notams`` and ``pireps`` are **nullable lists** — see the module docstring.
    """

    icao: str | None = None

    weather: BriefingWeather | None = None
    """``None`` when the briefing ran with ``include_weather=False``."""

    notams: list[BriefingNotam] | None = None
    """``None`` when NOTAMs were excluded, ``[]`` when there simply were none."""

    pireps: list[BriefingPirep] | None = None
    """``None`` when PIREPs were excluded (the default), ``[]`` when there were
    none in range."""


# ── responses ────────────────────────────────────────────────────────────────


class FlightBriefing(SkyLinkModel):
    """Structured briefing — ``GET /briefing/flight?format=json``."""

    origin: str | None = None
    destination: str | None = None

    summary: str | None = None
    """Two to three sentence overview of the whole flight."""

    critical_restrictions: list[BriefingRestriction] = Field(default_factory=list)
    """Empty list is the normal case, and it is a real ``[]`` here (unlike the
    per-airport ``notams``/``pireps``)."""

    origin_briefing: AirportBriefing | None = None
    destination_briefing: AirportBriefing | None = None

    data_included: list[str] = Field(default_factory=list)
    """Which sources actually made it in — subset of
    ``["metar", "taf", "notams", "pireps"]``."""

    disclaimer: str | None = None
    """Boilerplate legal text; the API always sends it."""


class FlightBriefingText(SkyLinkModel):
    """Rendered briefing — ``GET /briefing/flight?format=markdown|plain_text|html``.

    The endpoint always answers ``application/json``; the document itself sits in
    :attr:`briefing`. :meth:`skylink_api.resources.briefing.Briefing.flight`
    unwraps that field and hands back a bare ``str``, so this model is only
    needed when calling the endpoint through ``client.request()``.
    """

    origin: str | None = None
    destination: str | None = None

    format: str | None = None
    """The format that was rendered, echoed back."""

    briefing: str = ""
    """The rendered document. Defaults to an empty string rather than ``None``
    so the unwrapped return type stays a plain ``str``."""

    data_included: list[str] = Field(default_factory=list)
    disclaimer: str | None = None
