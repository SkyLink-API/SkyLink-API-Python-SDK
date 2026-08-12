"""Live flight status by flight number.

Structure follows :mod:`skylink_api.resources.weather`: builders first, then the
sync class, then its async mirror.

The namespace has a single method, so the client exposes it directly as
``sky.flight_status("BA123")`` (wired up separately); that shortcut and
:meth:`FlightStatus.get` share this module's builder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .._types import RequestOptions, RequestSpec
from ..models.flight_status import FlightStatusResponse

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncFlightStatus", "FlightStatus"]


# ── builders ─────────────────────────────────────────────────────────────────


def _flight_status_spec(flight_number: str) -> RequestSpec:
    """``GET /flight_status/{flight_number}``."""

    return RequestSpec(
        method="GET",
        path=f"/flight_status/{flight_number}",
        cast_to=FlightStatusResponse,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class FlightStatus:
    """Live status of a single flight.

    Reach it through the client rather than constructing it directly — the
    client surfaces this one-method namespace as a call on itself::

        with SkyLink(api_key="...") as sky:
            status = sky.flight_status("BA123")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def get(
        self,
        flight_number: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> FlightStatusResponse:
        """Status of one flight — ``GET /flight_status/{flight_number}``.

        Args:
            flight_number: IATA (``"BA123"``) or ICAO (``"BAW123"``) form, 2-10
                characters; ICAO is converted to IATA server-side. The API
                upper-cases and strips it, so ``"ba 123"`` works too.

        Note:
            The payload is scraped, and it shows. All fields are optional
            strings, empty values arrive as ``""`` or ``"--"`` rather than
            ``null``, and both ``departure`` and ``arrival`` may be empty
            objects. Times are airport-local (``"10:30"``) and dates have no
            year (``"11 Feb"``) — see
            :class:`~skylink_api.models.flight_status.FlightStatusResponse`.

            The two legs are **not** the same shape: ``departure`` has
            ``actual_time``/``actual_date``/``checkin``, ``arrival`` has
            ``estimated_time``/``estimated_date``/``baggage``.

        Raises:
            BadRequestError: empty flight number (400).
            NotFoundError: no such flight, or the source has no page for it (404).
            InternalServerError: the upstream source is blocking or down (500).
        """

        spec = _flight_status_spec(flight_number)
        return cast(FlightStatusResponse, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncFlightStatus:
    """Flight status on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`FlightStatus`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            status = await sky.flight_status("BA123")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def get(
        self,
        flight_number: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> FlightStatusResponse:
        """Status of one flight — ``GET /flight_status/{flight_number}``.

        Args:
            flight_number: IATA (``"BA123"``) or ICAO (``"BAW123"``) form, 2-10
                characters; ICAO is converted to IATA server-side. The API
                upper-cases and strips it, so ``"ba 123"`` works too.

        Note:
            The payload is scraped, and it shows. All fields are optional
            strings, empty values arrive as ``""`` or ``"--"`` rather than
            ``null``, and both ``departure`` and ``arrival`` may be empty
            objects. Times are airport-local (``"10:30"``) and dates have no
            year (``"11 Feb"``) — see
            :class:`~skylink_api.models.flight_status.FlightStatusResponse`.

            The two legs are **not** the same shape: ``departure`` has
            ``actual_time``/``actual_date``/``checkin``, ``arrival`` has
            ``estimated_time``/``estimated_date``/``baggage``.

        Raises:
            BadRequestError: empty flight number (400).
            NotFoundError: no such flight, or the source has no page for it (404).
            InternalServerError: the upstream source is blocking or down (500).
        """

        spec = _flight_status_spec(flight_number)
        result: Any = await self._client.execute(spec, request_options)
        return cast(FlightStatusResponse, result)
