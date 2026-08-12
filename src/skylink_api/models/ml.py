"""Models for the machine-learning endpoints (``/ml/*``).

Unlike most of the API this response is served through a backend
``response_model``, so its shape is guaranteed rather than scraped: every field
is required except ``aircraft_type``. The model is therefore strict about them —
a missing key here means the contract broke, not that a source went quiet.
"""

from __future__ import annotations

from pydantic import ConfigDict

from ._base import SkyLinkModel

__all__ = ["FlightTimePrediction"]


class FlightTimePrediction(SkyLinkModel):
    """Predicted gate-to-gate flight time (``GET /ml/flight-time``)."""

    # ``model_version`` collides with pydantic's reserved ``model_`` prefix;
    # clearing the protected namespaces keeps the wire name instead of renaming
    # the field. Merged with SkyLinkModel's config.
    model_config = ConfigDict(protected_namespaces=())

    origin: str
    """Origin airport, echoed back — ICAO code even if you asked by IATA."""

    destination: str
    """Destination airport, echoed back."""

    aircraft_type: str | None = None
    """The ``aircraft`` you asked for (``"B738"``); ``None`` when you omitted it
    and the prediction used a generic profile. The only optional field."""

    distance_nm: float
    """Great-circle distance in nautical miles."""

    estimated_minutes: int
    """Point estimate of the flight time in minutes."""

    estimated_hours_display: str
    """The same estimate pre-formatted for humans — ``"7h 23m"``. A **string**,
    not a number: parse :attr:`estimated_minutes` if you need arithmetic."""

    min_minutes: int
    """Lower bound of the estimate, in minutes."""

    max_minutes: int
    """Upper bound of the estimate, in minutes."""

    model_version: str
    """Version of the artifact that produced the prediction. Also tells you
    *which* engine answered: the trained model or the distance/speed fallback
    the API uses when the model is unavailable. The response shape is identical
    either way."""
