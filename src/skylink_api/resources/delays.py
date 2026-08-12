"""FAA National Airspace System delay alerts.

Structure follows ``resources/weather.py``: a module-level ``_*_spec()`` builder
owns the endpoint detail, and the sync/async classes are thin
``execute(spec, request_options)`` wrappers.

One method, two paths: :meth:`Delays.faa` hits ``/delays/faa`` with no argument
and ``/delays/faa/{icao}`` with one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .._types import RequestOptions, RequestSpec
from ..models.delays import FaaDelayResponse

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncDelays", "Delays"]


# ── builders ─────────────────────────────────────────────────────────────────


def _faa_spec(icao: str | None = None) -> RequestSpec:
    """``GET /delays/faa`` — or ``GET /delays/faa/{icao}`` when ``icao`` is given."""

    path = "/delays/faa" if icao is None else f"/delays/faa/{icao}"
    return RequestSpec(method="GET", path=path, cast_to=FaaDelayResponse)


# ── sync ─────────────────────────────────────────────────────────────────────


class Delays:
    """``sky.delays`` — live FAA NAS status.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            nas = sky.delays.faa()
            jfk = sky.delays.faa("KJFK")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def faa(
        self,
        icao: str | None = None,
        *,
        request_options: RequestOptions | None = None,
    ) -> FaaDelayResponse:
        """Active FAA delays — ``GET /delays/faa`` or ``GET /delays/faa/{icao}``.

        Args:
            icao: Optional 4-letter ICAO code. Omit it for the whole national
                picture; pass one (``"KJFK"``) to filter to that airport.

        Note:
            **US only.** The source is the FAA's operational status feed, so a
            non-US ICAO simply comes back empty rather than as an error.

            ``airspace_flow_programs`` is **not filtered** by the per-airport
            variant — flow programs are ATC-facility-level, so every active one
            is returned and counted in ``total_alerts`` regardless of the
            airport you asked about.

            Durations and times are **prose strings** (``"1 hour and 30
            minutes"``, ``"9:59 pm EST"``), forwarded exactly as the FAA prints
            them. Nothing here is a number or a ``datetime``.

            A quiet airspace is a normal ``200``: all four arrays empty,
            ``total_alerts=0`` and a human-readable ``message``. ``message`` is
            ``None`` whenever at least one alert exists.

        Raises:
            ServiceUnavailableError: the FAA feed could not be reached (503) —
                retried automatically.
            UnprocessableEntityError: ``icao`` is not exactly 4 characters (422).
        """

        return cast(FaaDelayResponse, self._client.execute(_faa_spec(icao), request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncDelays:
    """``sky.delays`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Delays`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            jfk = await sky.delays.faa("KJFK")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def faa(
        self,
        icao: str | None = None,
        *,
        request_options: RequestOptions | None = None,
    ) -> FaaDelayResponse:
        """Active FAA delays — ``GET /delays/faa`` or ``GET /delays/faa/{icao}``.

        Args:
            icao: Optional 4-letter ICAO code. Omit it for the whole national
                picture; pass one (``"KJFK"``) to filter to that airport.

        Note:
            **US only.** A non-US ICAO comes back empty rather than as an error.

            ``airspace_flow_programs`` is **not filtered** by the per-airport
            variant — flow programs are ATC-facility-level, so every active one
            is returned and counted in ``total_alerts``.

            Durations and times are **prose strings** (``"1 hour and 30
            minutes"``), forwarded exactly as the FAA prints them.

            A quiet airspace is a normal ``200``: empty arrays,
            ``total_alerts=0`` and a human-readable ``message`` (which is
            ``None`` whenever at least one alert exists).

        Raises:
            ServiceUnavailableError: the FAA feed could not be reached (503).
            UnprocessableEntityError: ``icao`` is not exactly 4 characters (422).
        """

        result: Any = await self._client.execute(_faa_spec(icao), request_options)
        return cast(FaaDelayResponse, result)
