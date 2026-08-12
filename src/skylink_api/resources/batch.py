"""``sky.batch`` — the same call for many identifiers, with bounded concurrency.

The API is strictly one-identifier-per-request: a dashboard showing twelve
airports issues twelve METAR calls. This namespace does that fan-out properly —
at most ``concurrency`` requests in flight, duplicates collapsed, and **one bad
identifier does not lose the other eleven answers**.

The shape of every method is the same::

    reports = sky.batch.metars(["KJFK", "EGLL", "ZZZZ"])
    # {"KJFK": Metar(...), "EGLL": Metar(...), "ZZZZ": NotFoundError(...)}

Rules that hold for all of them:

* **The key is the string you passed**, verbatim. Not the upper-cased or
  otherwise normalised form — otherwise looking the result up again would need
  the SDK's normalisation rules.
* **Duplicates are requested once** and share the single answer.
* **Key order is input order** (minus duplicates), so results zip back onto the
  caller's list.
* Only :class:`~skylink_api.SkyLinkError` lands in the dict. Anything else — a
  ``TypeError`` from a bug in this SDK, a ``KeyboardInterrupt`` — propagates, because
  swallowing it would turn a defect into a mysterious missing key.

Structure follows :mod:`skylink_api.resources.weather`: the request builders of
each underlying namespace are reused verbatim, so no endpoint knowledge is
duplicated here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar, cast

from .._exceptions import SkyLinkError
from .._types import RequestOptions, RequestSpec
from ..helpers.batch import DEFAULT_CONCURRENCY, amap_concurrent, map_concurrent
from ..helpers.idents import classify_airport_code
from ..models.airports import EnrichedAirport
from ..models.flight_status import FlightStatusResponse
from ..models.notams import NotamsResponse
from ..models.weather import Metar, Taf
from .airports import _search_spec
from .flight_status import _flight_status_spec
from .notams import _by_airport_spec
from .weather import _metar_spec, _taf_spec

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncBatch", "Batch"]

_ResultT = TypeVar("_ResultT")


# ── builders ─────────────────────────────────────────────────────────────────


def _unique(items: Iterable[str]) -> list[str]:
    """Input order, duplicates removed — the key order of every batch result."""

    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _airport_spec(code: str) -> RequestSpec:
    """``GET /airports/search`` for a code of unknown flavour.

    ``airports.search`` takes ``icao=`` or ``iata=``, never "code", so the
    parameter is chosen by shape: three letters is IATA, anything else is passed
    as ICAO. OurAirports pseudo-codes (``GB-0888``) go the ICAO route and come
    back as a 404 in the result dict — that endpoint genuinely cannot resolve
    them (see :func:`~skylink_api.helpers.idents.is_local_pseudocode`).
    """

    if classify_airport_code(code) == "iata":
        return _search_spec(iata=code)
    return _search_spec(icao=code)


def _collect(
    keys: Sequence[str],
    outcomes: Sequence[_ResultT | Exception],
) -> dict[str, _ResultT | SkyLinkError]:
    """Zip keys onto outcomes, keeping API errors as values and re-raising the rest.

    A non-:class:`SkyLinkError` exception is a bug in the SDK or in the caller's
    own code, and it is raised (first one, in key order) instead of being filed
    under a key where it would look like an API failure.
    """

    results: dict[str, _ResultT | SkyLinkError] = {}
    for key, outcome in zip(keys, outcomes, strict=True):
        if isinstance(outcome, SkyLinkError):
            results[key] = outcome
        elif isinstance(outcome, Exception):
            raise outcome
        else:
            results[key] = outcome
    return results


# ── sync ─────────────────────────────────────────────────────────────────────


class Batch:
    """``sky.batch`` — fan out one call over many identifiers.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            reports = sky.batch.metars(["KJFK", "EGLL", "UUEE"])
            for icao, report in reports.items():
                if isinstance(report, SkyLinkError):
                    print(icao, "failed:", report)

    The blocking version uses a thread pool. The client's
    :class:`httpx.Client` is thread safe and shared, so no extra connection pool
    is created; only the configured ``concurrency`` bounds how many requests are
    open at once.

    :mod:`skylink_api.helpers.batch` has ``successes()``, ``failures()`` and
    ``raise_for_errors()`` for reading the returned mapping.
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def _run(
        self,
        items: Iterable[str],
        build: Callable[[str], RequestSpec],
        *,
        concurrency: int,
        request_options: RequestOptions | None,
    ) -> dict[str, _ResultT | SkyLinkError]:
        keys = _unique(items)

        def call(key: str) -> _ResultT:
            return cast(_ResultT, self._client.execute(build(key), request_options))

        outcomes = map_concurrent(call, keys, concurrency=concurrency)
        return _collect(keys, outcomes)

    def metars(
        self,
        icaos: Iterable[str],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_options: RequestOptions | None = None,
    ) -> dict[str, Metar | SkyLinkError]:
        """METARs for many airports — ``GET /weather/metar/{icao}`` per code.

        Args:
            icaos: 4-letter ICAO codes. IATA codes are not accepted by the
                endpoint and come back as errors in the result.
            concurrency: Simultaneous requests, default 5 (RapidAPI quotas).
            request_options: Per-request overrides applied to **every** call.

        Returns:
            ``{code: Metar | SkyLinkError}`` keyed by the code exactly as given.
            A station that reports no current METAR is a
            :class:`~skylink_api.NotFoundError` value, not a missing key.

        Raises:
            ValueError: ``concurrency`` below 1 (before any request is sent).
        """

        return self._run(
            icaos,
            _metar_spec,
            concurrency=concurrency,
            request_options=request_options,
        )

    def tafs(
        self,
        icaos: Iterable[str],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_options: RequestOptions | None = None,
    ) -> dict[str, Taf | SkyLinkError]:
        """TAFs for many airports — ``GET /weather/taf/{icao}`` per code.

        Args:
            icaos: 4-letter ICAO codes.
            concurrency: Simultaneous requests, default 5.
            request_options: Per-request overrides applied to every call.

        Returns:
            ``{code: Taf | SkyLinkError}``. Many small fields issue METAR only,
            so a 404 here is normal and expected — hence the per-key error rather
            than an exception.
        """

        return self._run(
            icaos,
            _taf_spec,
            concurrency=concurrency,
            request_options=request_options,
        )

    def notams(
        self,
        icaos: Iterable[str],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_options: RequestOptions | None = None,
    ) -> dict[str, NotamsResponse | SkyLinkError]:
        """NOTAMs for many airports — ``GET /notams/{icao}`` per code.

        Args:
            icaos: 4-letter ICAO codes.
            concurrency: Simultaneous requests, default 5.
            request_options: Per-request overrides applied to every call.

        Returns:
            ``{code: NotamsResponse | SkyLinkError}``. An airport with no active
            NOTAMs is a normal response with ``notams=[]``, not an error.

        Note:
            The filters of
            :meth:`~skylink_api.resources.notams.Notams.by_airport`
            (``exclude_qcode``, ``exclude_scope``, ``include_future``) are not
            exposed here; call that method directly when you need them.
        """

        return self._run(
            icaos,
            _by_airport_spec,
            concurrency=concurrency,
            request_options=request_options,
        )

    def airports(
        self,
        codes: Iterable[str],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_options: RequestOptions | None = None,
    ) -> dict[str, EnrichedAirport | SkyLinkError]:
        """Enriched airports for many codes — ``GET /airports/search`` per code.

        Args:
            codes: ICAO (``"KJFK"``) or IATA (``"JFK"``) codes, mixed freely —
                three letters are sent as ``iata=``, everything else as ``icao=``.
            concurrency: Simultaneous requests, default 5.
            request_options: Per-request overrides applied to every call.

        Returns:
            ``{code: EnrichedAirport | SkyLinkError}``.

        Note:
            OurAirports pseudo-codes (``GB-0888``, returned by
            ``airports.search/location``) cannot be resolved by this endpoint and
            always come back as errors — filter them with
            :func:`~skylink_api.helpers.idents.is_local_pseudocode` first.
        """

        return self._run(
            codes,
            _airport_spec,
            concurrency=concurrency,
            request_options=request_options,
        )

    def flight_statuses(
        self,
        flight_numbers: Iterable[str],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_options: RequestOptions | None = None,
    ) -> dict[str, FlightStatusResponse | SkyLinkError]:
        """Live status of many flights — ``GET /flight_status/{number}`` per flight.

        Args:
            flight_numbers: IATA (``"BA123"``) or ICAO (``"BAW123"``) designators.
            concurrency: Simultaneous requests, default 5.
            request_options: Per-request overrides applied to every call.

        Returns:
            ``{number: FlightStatusResponse | SkyLinkError}``, keyed by the number
            as passed — the API upper-cases it internally, so ``"ba123"`` stays
            ``"ba123"`` here even though the payload echoes ``"BA123"``.

        Note:
            This payload is scraped: every field is an optional string and times
            are opaque. See
            :meth:`~skylink_api.resources.flight_status.FlightStatus.get`.
        """

        return self._run(
            flight_numbers,
            _flight_status_spec,
            concurrency=concurrency,
            request_options=request_options,
        )


# ── async ────────────────────────────────────────────────────────────────────


class AsyncBatch:
    """``sky.batch`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Batch` — same keys, same ordering, same error handling;
    concurrency is bounded by an :class:`asyncio.Semaphore` instead of a thread
    pool::

        async with AsyncSkyLink(api_key="...") as sky:
            reports = await sky.batch.metars(["KJFK", "EGLL"])
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def _run(
        self,
        items: Iterable[str],
        build: Callable[[str], RequestSpec],
        *,
        concurrency: int,
        request_options: RequestOptions | None,
    ) -> dict[str, _ResultT | SkyLinkError]:
        keys = _unique(items)

        async def call(key: str) -> _ResultT:
            result: Any = await self._client.execute(build(key), request_options)
            return cast(_ResultT, result)

        outcomes = await amap_concurrent(call, keys, concurrency=concurrency)
        return _collect(keys, outcomes)

    async def metars(
        self,
        icaos: Iterable[str],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_options: RequestOptions | None = None,
    ) -> dict[str, Metar | SkyLinkError]:
        """METARs for many airports — ``GET /weather/metar/{icao}`` per code.

        Args:
            icaos: 4-letter ICAO codes.
            concurrency: Simultaneous requests, default 5 (RapidAPI quotas).
            request_options: Per-request overrides applied to every call.

        Returns:
            ``{code: Metar | SkyLinkError}`` keyed by the code exactly as given.
        """

        return await self._run(
            icaos,
            _metar_spec,
            concurrency=concurrency,
            request_options=request_options,
        )

    async def tafs(
        self,
        icaos: Iterable[str],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_options: RequestOptions | None = None,
    ) -> dict[str, Taf | SkyLinkError]:
        """TAFs for many airports — ``GET /weather/taf/{icao}`` per code.

        Args:
            icaos: 4-letter ICAO codes.
            concurrency: Simultaneous requests, default 5.
            request_options: Per-request overrides applied to every call.

        Returns:
            ``{code: Taf | SkyLinkError}``; a field that issues no TAF is a 404
            value, which is normal.
        """

        return await self._run(
            icaos,
            _taf_spec,
            concurrency=concurrency,
            request_options=request_options,
        )

    async def notams(
        self,
        icaos: Iterable[str],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_options: RequestOptions | None = None,
    ) -> dict[str, NotamsResponse | SkyLinkError]:
        """NOTAMs for many airports — ``GET /notams/{icao}`` per code.

        Args:
            icaos: 4-letter ICAO codes.
            concurrency: Simultaneous requests, default 5.
            request_options: Per-request overrides applied to every call.

        Returns:
            ``{code: NotamsResponse | SkyLinkError}``. The per-airport filters are
            not exposed here; use ``sky.notams.by_airport`` for those.
        """

        return await self._run(
            icaos,
            _by_airport_spec,
            concurrency=concurrency,
            request_options=request_options,
        )

    async def airports(
        self,
        codes: Iterable[str],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_options: RequestOptions | None = None,
    ) -> dict[str, EnrichedAirport | SkyLinkError]:
        """Enriched airports for many codes — ``GET /airports/search`` per code.

        Args:
            codes: ICAO or IATA codes, mixed freely (three letters → ``iata=``).
            concurrency: Simultaneous requests, default 5.
            request_options: Per-request overrides applied to every call.

        Returns:
            ``{code: EnrichedAirport | SkyLinkError}``. OurAirports pseudo-codes
            cannot be resolved and always come back as errors.
        """

        return await self._run(
            codes,
            _airport_spec,
            concurrency=concurrency,
            request_options=request_options,
        )

    async def flight_statuses(
        self,
        flight_numbers: Iterable[str],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_options: RequestOptions | None = None,
    ) -> dict[str, FlightStatusResponse | SkyLinkError]:
        """Live status of many flights — ``GET /flight_status/{number}`` per flight.

        Args:
            flight_numbers: IATA or ICAO designators.
            concurrency: Simultaneous requests, default 5.
            request_options: Per-request overrides applied to every call.

        Returns:
            ``{number: FlightStatusResponse | SkyLinkError}``, keyed by the number
            as passed.
        """

        return await self._run(
            flight_numbers,
            _flight_status_spec,
            concurrency=concurrency,
            request_options=request_options,
        )
