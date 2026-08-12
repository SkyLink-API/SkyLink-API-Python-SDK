"""Flight CO₂ estimation (``/carbon/*``).

Follows the layout of :mod:`skylink_api.resources.weather`: builders first, then
the sync class, then the async mirror.

The one addition is a **client-side precondition**. ``/carbon/estimate`` needs
either a callsign or both airports; sending neither is a guaranteed ``422``, so
:func:`_estimate_spec` raises :class:`ValueError` before any socket is opened.
The check lives in the builder rather than in the two methods so sync and async
cannot drift apart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .._types import RequestOptions, RequestSpec
from ..models.carbon import CarbonEstimate

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncCarbon", "Carbon"]


# ── builders ─────────────────────────────────────────────────────────────────


def _estimate_spec(
    *,
    departure_icao: str | None = None,
    arrival_icao: str | None = None,
    callsign: str | None = None,
    aircraft_type: str | None = None,
    passengers: int | None = None,
    include_rfi: bool = False,
) -> RequestSpec:
    """``GET /carbon/estimate``.

    Raises:
        ValueError: neither ``callsign`` nor both airports were given.
    """

    if not callsign and not (departure_icao and arrival_icao):
        raise ValueError(
            "carbon.estimate() needs either callsign=... or both "
            "departure_icao=... and arrival_icao=..."
        )

    return RequestSpec(
        method="GET",
        path="/carbon/estimate",
        query={
            "departure_icao": departure_icao,
            "arrival_icao": arrival_icao,
            "callsign": callsign,
            "aircraft_type": aircraft_type,
            "passengers": passengers,
            "include_rfi": include_rfi,
        },
        cast_to=CarbonEstimate,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Carbon:
    """``sky.carbon`` — CO₂ emission estimates.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            estimate = sky.carbon.estimate(departure_icao="EGLL", arrival_icao="KJFK")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def estimate(
        self,
        *,
        departure_icao: str | None = None,
        arrival_icao: str | None = None,
        callsign: str | None = None,
        aircraft_type: str | None = None,
        passengers: int | None = None,
        include_rfi: bool = False,
        request_options: RequestOptions | None = None,
    ) -> CarbonEstimate:
        """Estimate a flight's CO₂ emissions — ``GET /carbon/estimate``.

        Two ways to identify the flight:

        * **by airports** — ``departure_icao`` + ``arrival_icao``; the distance
          is the great circle between them;
        * **by callsign** — the route comes from VRS standing data and the
          distance from the most recent matching flight of the last 7 days
          (actual flown path), falling back to the great circle.

        Supplying both is allowed and is the most precise option: the airports
        are taken as given while the callsign still contributes the flown
        distance and the aircraft type.

        Args:
            departure_icao: 4-letter ICAO code. Required unless ``callsign``.
            arrival_icao: 4-letter ICAO code, different from the departure.
                Required unless ``callsign``.
            callsign: Flight callsign (``"BAW117"``). Required unless both
                airports are given.
            aircraft_type: ICAO type code (``"B77W"``). Omit to infer it from
                the callsign's recent flights, or to fall back to the default
                category — which drops ``confidence`` to ``"medium"``.
            passengers: 1-900. Omit to use the aircraft category's typical seat
                count (reported back as ``passengers_source``).
            include_rfi: Also compute CO₂-equivalent with the 1.9x Radiative
                Forcing Index. Without it, ``co2_equivalent_kg_total`` and
                ``co2_equivalent_kg_per_passenger`` come back ``None`` — the
                keys are present either way.

        Note:
            ``callsign``, ``callsign_resolved`` and ``route_confidence`` are
            **omitted from the payload entirely** when the request did not pass
            a callsign — they are not sent as ``null``. Use
            ``"callsign" in estimate.model_fields_set`` to tell the two apart.

        Raises:
            ValueError: called with neither a callsign nor both airports
                (raised locally, before the request).
            NotFoundError: an airport code is unknown, or a callsign resolved to
                no route at all (404).
            UnprocessableEntityError: malformed ICAO code, identical departure
                and arrival, or ``passengers`` out of range (422).
        """

        spec = _estimate_spec(
            departure_icao=departure_icao,
            arrival_icao=arrival_icao,
            callsign=callsign,
            aircraft_type=aircraft_type,
            passengers=passengers,
            include_rfi=include_rfi,
        )
        return cast(CarbonEstimate, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncCarbon:
    """``sky.carbon`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Carbon`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            estimate = await sky.carbon.estimate(callsign="BAW117")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def estimate(
        self,
        *,
        departure_icao: str | None = None,
        arrival_icao: str | None = None,
        callsign: str | None = None,
        aircraft_type: str | None = None,
        passengers: int | None = None,
        include_rfi: bool = False,
        request_options: RequestOptions | None = None,
    ) -> CarbonEstimate:
        """Estimate a flight's CO₂ emissions — ``GET /carbon/estimate``.

        Two ways to identify the flight:

        * **by airports** — ``departure_icao`` + ``arrival_icao``; the distance
          is the great circle between them;
        * **by callsign** — the route comes from VRS standing data and the
          distance from the most recent matching flight of the last 7 days
          (actual flown path), falling back to the great circle.

        Supplying both is allowed and is the most precise option: the airports
        are taken as given while the callsign still contributes the flown
        distance and the aircraft type.

        Args:
            departure_icao: 4-letter ICAO code. Required unless ``callsign``.
            arrival_icao: 4-letter ICAO code, different from the departure.
                Required unless ``callsign``.
            callsign: Flight callsign (``"BAW117"``). Required unless both
                airports are given.
            aircraft_type: ICAO type code (``"B77W"``). Omit to infer it from
                the callsign's recent flights, or to fall back to the default
                category — which drops ``confidence`` to ``"medium"``.
            passengers: 1-900. Omit to use the aircraft category's typical seat
                count (reported back as ``passengers_source``).
            include_rfi: Also compute CO₂-equivalent with the 1.9x Radiative
                Forcing Index. Without it, ``co2_equivalent_kg_total`` and
                ``co2_equivalent_kg_per_passenger`` come back ``None`` — the
                keys are present either way.

        Note:
            ``callsign``, ``callsign_resolved`` and ``route_confidence`` are
            **omitted from the payload entirely** when the request did not pass
            a callsign — they are not sent as ``null``. Use
            ``"callsign" in estimate.model_fields_set`` to tell the two apart.

        Raises:
            ValueError: called with neither a callsign nor both airports
                (raised locally, before the request).
            NotFoundError: an airport code is unknown, or a callsign resolved to
                no route at all (404).
            UnprocessableEntityError: malformed ICAO code, identical departure
                and arrival, or ``passengers`` out of range (422).
        """

        spec = _estimate_spec(
            departure_icao=departure_icao,
            arrival_icao=arrival_icao,
            callsign=callsign,
            aircraft_type=aircraft_type,
            passengers=passengers,
            include_rfi=include_rfi,
        )
        result: Any = await self._client.execute(spec, request_options)
        return cast(CarbonEstimate, result)
