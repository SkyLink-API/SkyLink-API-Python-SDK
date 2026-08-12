"""Models for ``GET /notams/{icao}``.

The trap here is the timestamps. ``effective`` and ``expiration`` are **opaque
strings** and the feed mixes formats within a single response, so the SDK never
parses them:

============================  ==================================================
Value                         Meaning
============================  ==================================================
``"2026-07-16T21:30:00Z"``    ISO 8601 (AIXM 5.1 messages, may carry an offset)
``"202607162130"``            compact ``YYYYMMDDHHMM``, UTC
``"2607162130"``              compact ``YYMMDDHHMM``, UTC (older FAA form)
``"2607302215EST"``           any of the above plus an ``EST`` suffix, meaning
                              *estimated* end time — not the US Eastern zone
``"PERM"``                    permanent, no expiration
============================  ==================================================

Typing these as ``datetime`` would either lose the ``EST``/``PERM`` markers or
raise on a format the SDK has not seen, and NOTAM text is exactly the place where
silently mangling a time is dangerous. If you need a real datetime, parse them
yourself and decide what to do with the sentinels.

Everything except ``raw`` is optional: the originating authority controls which
ICAO items it files, so US domestic NOTAMs often arrive with no Q-line, and the
``D)`` schedule item only exists on NOTAMs that carry one.
"""

from __future__ import annotations

from pydantic import Field

from ._base import SkyLinkModel

__all__ = ["NotamEntry", "NotamsResponse"]


class NotamEntry(SkyLinkModel):
    """A single Notice to Air Missions."""

    raw: str | None = None
    """The whole NOTAM in standard ICAO format: the identification line followed
    by the ``Q) A) B) C) D) E)`` and ``F)/G)`` items, newline separated. Rendered
    per request, so it is current even for records stored before the formatter
    existed."""

    notam_id: str | None = None
    """ICAO series identifier, ``"A2161/2026"``."""

    notam_id_domestic: str | None = None
    """FAA domestic identifier, ``"07/2161"`` — the form shown on
    notams.aim.faa.gov. Backfilled per request when the stored record lacks it,
    but still ``None`` for non-US NOTAMs."""

    type: str | None = None
    """``"N"`` (new), ``"R"`` (replacement) or ``"C"`` (cancellation). Kept as
    ``str``: the feed is upstream data and may grow a value."""

    location: str | None = None
    """Identifier of the affected location, usually the aerodrome ICAO code."""

    effective: str | None = None
    """Start of validity. **Opaque string** — ISO 8601 or compact
    ``YYMMDDHHMM``/``YYYYMMDDHHMM``, see the module docstring. Never parsed."""

    expiration: str | None = None
    """End of validity. **Opaque string**, same formats as :attr:`effective`,
    plus ``"PERM"`` for a permanent NOTAM and a trailing ``"EST"`` when the end
    time is only estimated. Never parsed."""

    body: str | None = None
    """The ``E)`` item — the actual notice text (``"RWY 12L/30R CLSD"``)."""

    schedule: str | None = None
    """The ``D)`` item, e.g. ``"MON-FRI 0600-1800"``. Present only on NOTAMs
    that are active on a recurring schedule."""

    lower_limit: str | None = None
    """Lower altitude limit as filed — ``"SFC"``, ``"FL050"``. A string, not a
    number: the units are part of the value."""

    upper_limit: str | None = None
    """Upper altitude limit as filed — ``"FL195"``, ``"UNL"``."""

    affected_fir: str | None = None
    """FIR/ARTCC the NOTAM is filed under (``"KZNY"``, ``"EGTT"``)."""

    q_code: str | None = None
    """ICAO selection code, ``"QMRLC"``. Its first two letters after ``Q`` are
    the subject category used by ``exclude_qcode``."""

    qline: str | None = None
    """The full ``Q)`` line when the feed supplied one; US domestic NOTAMs are
    frequently filed without it."""

    scope: str | None = None
    """``"AERODROME"`` — filed at this airport — or ``"FIR"``, an en-route notice
    merged in from the airport's FIR/ARTCC the way an AIS aerodrome PIB does.
    Filter FIR-wide noise out server-side with ``exclude_scope="FIR"``."""

    status: str | None = None
    """``"ACTIVE"`` (already in effect) or ``"FUTURE"`` (effective date still
    ahead). ``"FUTURE"`` entries are only returned when the request set
    ``include_future=True``; expired NOTAMs are never returned at all."""


class NotamsResponse(SkyLinkModel):
    """Envelope of ``GET /notams/{icao}``."""

    icao: str | None = None
    """The requested ICAO code, upper-cased."""

    notams: list[NotamEntry] = Field(default_factory=list)
    total: int = 0
    """Number of entries in :attr:`notams` — there is no pagination."""
