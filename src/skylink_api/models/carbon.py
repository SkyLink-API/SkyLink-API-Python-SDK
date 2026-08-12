"""Models for ``GET /carbon/estimate``.

One response model, but with a wrinkle worth spelling out: three of its keys are
**conditionally absent** rather than ``null``. ``services/v31/carbon_service.py``
builds the base dict and only then does::

    if callsign:
        result["callsign"] = callsign
        result["callsign_resolved"] = callsign_resolved
        if route_confidence:
            result["route_confidence"] = route_confidence

So a request made with ``departure_icao``/``arrival_icao`` comes back without
those keys at all, and ``route_confidence`` can be missing even when a callsign
*was* supplied (the VRS lookup may have been skipped because both airports were
given explicitly). They are therefore plain optionals here — and code that needs
to tell "absent" from "present but null" can ask pydantic::

    "callsign" in estimate.model_fields_set

The other nullable pair, ``co2_equivalent_kg_total`` /
``co2_equivalent_kg_per_passenger``, is the opposite case: those keys are
*always* present but hold ``None`` unless the call passed ``include_rfi=True``.
"""

from __future__ import annotations

from ._base import SkyLinkModel
from .common import Number

__all__ = ["CarbonEstimate"]


class CarbonEstimate(SkyLinkModel):
    """A CO₂ estimate for one flight (ICAO Doc 9988 methodology).

    ``CO2_kg = distance_km * EF(aircraft_category) / load_factor``. Everything is
    an estimate built from category averages — see :attr:`confidence`.
    """

    departure_icao: str | None = None
    """Departure airport, upper-cased. Resolved from the callsign when the call
    only supplied one."""

    arrival_icao: str | None = None
    """Arrival airport, upper-cased."""

    aircraft_type: str | None = None
    """ICAO type code actually used (``"B77W"``). ``None`` when neither the
    request nor the historical flight lookup produced one — the estimate then
    falls back to the default category."""

    aircraft_category: str | None = None
    """Emission-factor bucket the type mapped to: ``narrow_body_small``,
    ``narrow_body_medium``, ``wide_body_small``, ``wide_body_large``,
    ``very_large``, ``regional_jet``, ``turboprop``, ``business_jet``."""

    distance_km: Number | None = None
    """Great-circle or actually-flown distance in **kilometres** — see
    :attr:`distance_source`."""

    distance_nm: Number | None = None
    """The same distance in nautical miles."""

    passengers: int | None = None
    """Passenger count used for the per-passenger figure. Echoes the request
    when it supplied one, otherwise the category's typical seat count."""

    passengers_source: str | None = None
    """``"provided"`` or ``"category_default"``."""

    co2_kg_total: Number | None = None
    """Whole-aircraft CO₂ in kilograms."""

    co2_kg_per_passenger: Number | None = None
    """Per-passenger CO₂ in kilograms (already divided by the load factor)."""

    rfi_applied: bool | None = None
    """Whether the Radiative Forcing Index multiplier was applied — echoes the
    request's ``include_rfi``."""

    rfi_factor: Number | None = None
    """The RFI multiplier the API would use (1.9, the IPCC value). Reported
    even when it was **not** applied, so it is not a flag."""

    co2_equivalent_kg_total: Number | None = None
    """Total CO₂-equivalent including non-CO₂ effects (contrails, ozone).

    The key is always present but is ``None`` unless ``include_rfi=True``.
    """

    co2_equivalent_kg_per_passenger: Number | None = None
    """Per-passenger CO₂-equivalent; ``None`` without ``include_rfi=True``."""

    methodology: str | None = None
    """Always ``"ICAO-Doc9988"`` today."""

    confidence: str | None = None
    """``"high"`` when the aircraft type mapped to a known category, ``"low"``
    when a type was given but unknown, ``"medium"`` when none was given."""

    distance_source: str | None = None
    """``"haversine"`` (great circle between the two airports), ``"adsb_track"``
    (actual flown path of a recent flight) or ``"historical_flight"``
    (pre-computed distance of the last matching flight)."""

    notes: str | None = None
    """Free-form caveat about what the number does and does not include."""

    # ── conditionally absent: only sent when the request passed a callsign ────

    callsign: str | None = None
    """The callsign, upper-cased. **Key absent** when the request identified the
    flight by airports instead."""

    callsign_resolved: bool | None = None
    """``True`` when the callsign actually produced a route or a historical
    distance, ``False`` when it was accepted but nothing was found.
    **Key absent** without a callsign in the request."""

    route_confidence: str | None = None
    """Confidence of the VRS route lookup (``"high"``/``"low"``). **Key absent**
    both without a callsign *and* when the route lookup was skipped because the
    request already supplied both airports."""
