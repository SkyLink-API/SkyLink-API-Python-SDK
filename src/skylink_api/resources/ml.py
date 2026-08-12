"""Machine-learning predictions (``/ml/*``).

Structure follows :mod:`skylink_api.resources.weather`: builders first, then the
sync class, then its async mirror.

One quirk worth knowing: the endpoint's query parameters are literally named
``from`` and ``to``. ``from`` is a Python keyword, so the SDK takes ``origin``
and ``destination`` and the builder renames them on the way out. The wire format
is therefore ``/ml/flight-time?from=KJFK&to=EGLL`` even though your call says
``origin=`` and ``destination=``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .._types import RequestOptions, RequestSpec
from ..models.ml import FlightTimePrediction

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncMl", "Ml"]


# ── builders ─────────────────────────────────────────────────────────────────


def _flight_time_spec(
    *,
    origin: str,
    destination: str,
    aircraft: str | None = None,
) -> RequestSpec:
    """``GET /ml/flight-time``.

    Maps ``origin``/``destination`` onto the reserved-word query keys ``from``
    and ``to``. This rename happens exactly here — nothing downstream knows
    about it.
    """

    return RequestSpec(
        method="GET",
        path="/ml/flight-time",
        query={"from": origin, "to": destination, "aircraft": aircraft},
        cast_to=FlightTimePrediction,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Ml:
    """``sky.ml`` — model-backed predictions.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            eta = sky.ml.flight_time(origin="KJFK", destination="EGLL")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def flight_time(
        self,
        *,
        origin: str,
        destination: str,
        aircraft: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> FlightTimePrediction:
        """Predict gate-to-gate flight time — ``GET /ml/flight-time``.

        Args:
            origin: Origin airport, ICAO or IATA (3-4 characters). Sent as the
                ``from`` query parameter.
            destination: Destination airport, ICAO or IATA. Sent as ``to``.
            aircraft: Aircraft type code, max 4 characters (``"B738"``, ``"A320"``,
                ``"C172"``). Omit it for a generic profile.

        Note:
            When the trained model is unavailable the API falls back to a
            distance/speed calculation. The response shape is identical either
            way — ``model_version`` tells you which one answered.

            ``estimated_hours_display`` is a display string (``"7h 23m"``); use
            ``estimated_minutes`` for arithmetic.

        Raises:
            NotFoundError: one of the airport codes is unknown (404).
            UnprocessableEntityError: a code is not 3-4 characters, or
                ``aircraft`` is longer than 4 (422).
            InternalServerError: prediction failed (500).
        """

        spec = _flight_time_spec(origin=origin, destination=destination, aircraft=aircraft)
        return cast(FlightTimePrediction, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncMl:
    """``sky.ml`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Ml`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            eta = await sky.ml.flight_time(origin="KJFK", destination="EGLL")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def flight_time(
        self,
        *,
        origin: str,
        destination: str,
        aircraft: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> FlightTimePrediction:
        """Predict gate-to-gate flight time — ``GET /ml/flight-time``.

        Args:
            origin: Origin airport, ICAO or IATA (3-4 characters). Sent as the
                ``from`` query parameter.
            destination: Destination airport, ICAO or IATA. Sent as ``to``.
            aircraft: Aircraft type code, max 4 characters (``"B738"``). Omit it
                for a generic profile.

        Note:
            When the trained model is unavailable the API falls back to a
            distance/speed calculation; the shape is identical and
            ``model_version`` tells the two apart. ``estimated_hours_display``
            is a display string (``"7h 23m"``).

        Raises:
            NotFoundError: one of the airport codes is unknown (404).
            UnprocessableEntityError: a code is not 3-4 characters, or
                ``aircraft`` is longer than 4 (422).
            InternalServerError: prediction failed (500).
        """

        spec = _flight_time_spec(origin=origin, destination=destination, aircraft=aircraft)
        result: Any = await self._client.execute(spec, request_options)
        return cast(FlightTimePrediction, result)
