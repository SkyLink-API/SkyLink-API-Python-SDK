"""AI generated pre-flight briefings — structured, rendered or as a PDF.

Two mechanics that no other namespace needs:

* :meth:`Briefing.flight` is polymorphic in ``format``. ``format="json"`` yields
  a :class:`~skylink_api.models.briefing.FlightBriefing`; the three text formats
  yield a plain ``str``. ``@overload`` on the ``Literal`` values makes the static
  type follow the argument.
* :meth:`Briefing.pdf` is the API's only binary endpoint, so its spec sets
  ``response_kind="bytes"`` and the method returns ``bytes``.

Both carry their own read timeout. A briefing is written by a language model over
the weather and NOTAMs of two airports and takes tens of seconds — well past the
client-wide 30 s default — so every spec here sets
:data:`~skylink_api._constants.BRIEFING_TIMEOUT`. It is a *default*, not a
ceiling: ``request_options={"timeout": ...}`` still wins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast, overload

from .._constants import BRIEFING_TIMEOUT
from .._types import RequestOptions, RequestSpec
from ..models.briefing import FlightBriefing, FlightBriefingText

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncBriefing", "Briefing"]

#: Rendering formats that return a document string rather than a structure.
BriefingTextFormat = Literal["markdown", "plain_text", "html"]

#: Every accepted value of the ``format`` query parameter.
BriefingFormat = Literal["json", "markdown", "plain_text", "html"]


# ── builders ─────────────────────────────────────────────────────────────────


def _flight_spec(
    *,
    origin: str,
    destination: str,
    include_weather: bool = True,
    include_notams: bool = True,
    include_pireps: bool = False,
    format: BriefingFormat = "json",
) -> RequestSpec:
    """``GET /briefing/flight``.

    ``cast_to`` switches on ``format``: the text formats are still JSON on the
    wire (an envelope with a ``briefing`` string), so they are parsed as
    :class:`FlightBriefingText` and unwrapped by the caller.
    """

    return RequestSpec(
        method="GET",
        path="/briefing/flight",
        query={
            "origin": origin,
            "destination": destination,
            "include_weather": include_weather,
            "include_notams": include_notams,
            "include_pireps": include_pireps,
            "format": format,
        },
        cast_to=FlightBriefing if format == "json" else FlightBriefingText,
        timeout=BRIEFING_TIMEOUT,
    )


def _pdf_spec(
    *,
    departure_icao: str,
    arrival_icao: str,
    flight_number: str | None = None,
) -> RequestSpec:
    """``GET /briefing/pdf`` — the only endpoint that does not answer JSON."""

    return RequestSpec(
        method="GET",
        path="/briefing/pdf",
        query={
            "departure_icao": departure_icao,
            "arrival_icao": arrival_icao,
            "flight_number": flight_number,
        },
        response_kind="bytes",
        timeout=BRIEFING_TIMEOUT,
    )


def _unwrap_text(result: Any) -> str:
    """Pull the rendered document out of the text-format envelope."""

    return cast(FlightBriefingText, result).briefing


# ── sync ─────────────────────────────────────────────────────────────────────


class Briefing:
    """``sky.briefing`` — pre-flight briefings.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            report = sky.briefing.flight(origin="KJFK", destination="EGLL")
            pdf = sky.briefing.pdf(departure_icao="KJFK", arrival_icao="EGLL")
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    @overload
    def flight(
        self,
        *,
        origin: str,
        destination: str,
        include_weather: bool = True,
        include_notams: bool = True,
        include_pireps: bool = False,
        format: Literal["json"] = "json",
        request_options: RequestOptions | None = None,
    ) -> FlightBriefing: ...

    @overload
    def flight(
        self,
        *,
        origin: str,
        destination: str,
        include_weather: bool = True,
        include_notams: bool = True,
        include_pireps: bool = False,
        format: BriefingTextFormat,
        request_options: RequestOptions | None = None,
    ) -> str: ...

    def flight(
        self,
        *,
        origin: str,
        destination: str,
        include_weather: bool = True,
        include_notams: bool = True,
        include_pireps: bool = False,
        format: BriefingFormat = "json",
        request_options: RequestOptions | None = None,
    ) -> FlightBriefing | str:
        """Briefing for a route — ``GET /briefing/flight``.

        Args:
            origin: Origin ICAO code, exactly 4 letters. IATA is not accepted.
            destination: Destination ICAO code, exactly 4 letters.
            include_weather: Include METAR and TAF for both airports.
            include_notams: Include active NOTAMs.
            include_pireps: Include PIREPs within 100 nm from the last 3 hours.
                Off by default because it is the slowest source.
            format: ``"json"`` (default) returns a structured
                :class:`~skylink_api.models.briefing.FlightBriefing`;
                ``"markdown"``, ``"plain_text"`` and ``"html"`` return the
                rendered document as a ``str``.

        Returns:
            A :class:`~skylink_api.models.briefing.FlightBriefing` for
            ``format="json"``, otherwise the document itself.

        Note:
            The text formats are **not** served as ``text/*`` — the endpoint
            always answers JSON and puts the document in a ``briefing`` field.
            This method unwraps it, so the contract is a bare string. Use
            ``client.request()`` with
            :class:`~skylink_api.models.briefing.FlightBriefingText` if the
            surrounding metadata is needed.

            In the structured form, ``origin_briefing.notams`` /
            ``.pireps`` are ``None`` (not ``[]``) whenever the matching
            ``include_*`` flag was ``False``.

        Warning:
            **This is the slowest call in the SDK.** The briefing is written by a
            language model over both airports' weather and NOTAMs; measured live
            on 2026-08-15 it took 30-85 s depending on ``format``. The method
            therefore raises its own read timeout to
            :data:`~skylink_api._constants.BRIEFING_TIMEOUT` (180 s) instead of
            the client's 30 s default — otherwise a perfectly healthy request
            times out, gets retried three times, and fails after two minutes.

            Retries are *not* disabled, because a real 503 should still be
            retried. In a request/response context where two minutes is too long
            to wait, cap it yourself::

                sky.briefing.flight(
                    origin="KJFK",
                    destination="KLAX",
                    request_options={"timeout": 90.0, "max_retries": 0},
                )

        Raises:
            BadRequestError: all three ``include_*`` flags are ``False`` (400).
            NotFoundError: either ICAO code is unknown (404).
            ServiceUnavailableError: the briefing model or one of its data
                sources is unavailable (503).
        """

        spec = _flight_spec(
            origin=origin,
            destination=destination,
            include_weather=include_weather,
            include_notams=include_notams,
            include_pireps=include_pireps,
            format=format,
        )
        result = self._client.execute(spec, request_options)
        if format == "json":
            return cast(FlightBriefing, result)
        return _unwrap_text(result)

    def pdf(
        self,
        *,
        departure_icao: str,
        arrival_icao: str,
        flight_number: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> bytes:
        """Briefing as a printable PDF — ``GET /briefing/pdf``.

        Args:
            departure_icao: Departure ICAO code.
            arrival_icao: Arrival ICAO code; must differ from the departure.
            flight_number: Optional, printed in the document header (``"BA117"``).

        Returns:
            The PDF as raw ``bytes`` — the body starts with the ``%PDF`` magic
            bytes. Nothing is streamed; the API buffers the whole document.

        Raises:
            UnprocessableEntityError: identical or malformed airport codes (422).
            InternalServerError: PDF rendering failed (500).
            ServiceUnavailableError: an underlying data source is down (503).
        """

        spec = _pdf_spec(
            departure_icao=departure_icao,
            arrival_icao=arrival_icao,
            flight_number=flight_number,
        )
        return cast(bytes, self._client.execute(spec, request_options))


# ── async ────────────────────────────────────────────────────────────────────


class AsyncBriefing:
    """``sky.briefing`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Briefing`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            report = await sky.briefing.flight(origin="KJFK", destination="EGLL")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    @overload
    async def flight(
        self,
        *,
        origin: str,
        destination: str,
        include_weather: bool = True,
        include_notams: bool = True,
        include_pireps: bool = False,
        format: Literal["json"] = "json",
        request_options: RequestOptions | None = None,
    ) -> FlightBriefing: ...

    @overload
    async def flight(
        self,
        *,
        origin: str,
        destination: str,
        include_weather: bool = True,
        include_notams: bool = True,
        include_pireps: bool = False,
        format: BriefingTextFormat,
        request_options: RequestOptions | None = None,
    ) -> str: ...

    async def flight(
        self,
        *,
        origin: str,
        destination: str,
        include_weather: bool = True,
        include_notams: bool = True,
        include_pireps: bool = False,
        format: BriefingFormat = "json",
        request_options: RequestOptions | None = None,
    ) -> FlightBriefing | str:
        """Briefing for a route — ``GET /briefing/flight``.

        Args:
            origin: Origin ICAO code, exactly 4 letters. IATA is not accepted.
            destination: Destination ICAO code, exactly 4 letters.
            include_weather: Include METAR and TAF for both airports.
            include_notams: Include active NOTAMs.
            include_pireps: Include PIREPs within 100 nm from the last 3 hours.
                Off by default because it is the slowest source.
            format: ``"json"`` (default) returns a structured
                :class:`~skylink_api.models.briefing.FlightBriefing`;
                ``"markdown"``, ``"plain_text"`` and ``"html"`` return the
                rendered document as a ``str``.

        Returns:
            A :class:`~skylink_api.models.briefing.FlightBriefing` for
            ``format="json"``, otherwise the document itself.

        Note:
            The text formats are **not** served as ``text/*`` — the endpoint
            always answers JSON and puts the document in a ``briefing`` field.
            This method unwraps it, so the contract is a bare string. Use
            ``client.request()`` with
            :class:`~skylink_api.models.briefing.FlightBriefingText` if the
            surrounding metadata is needed.

            In the structured form, ``origin_briefing.notams`` /
            ``.pireps`` are ``None`` (not ``[]``) whenever the matching
            ``include_*`` flag was ``False``.

        Warning:
            **This is the slowest call in the SDK.** The briefing is written by a
            language model over both airports' weather and NOTAMs; measured live
            on 2026-08-15 it took 30-85 s depending on ``format``. The method
            therefore raises its own read timeout to
            :data:`~skylink_api._constants.BRIEFING_TIMEOUT` (180 s) instead of
            the client's 30 s default — otherwise a perfectly healthy request
            times out, gets retried three times, and fails after two minutes.

            Retries are *not* disabled, because a real 503 should still be
            retried. In a request/response context where two minutes is too long
            to wait, cap it yourself::

                sky.briefing.flight(
                    origin="KJFK",
                    destination="KLAX",
                    request_options={"timeout": 90.0, "max_retries": 0},
                )

        Raises:
            BadRequestError: all three ``include_*`` flags are ``False`` (400).
            NotFoundError: either ICAO code is unknown (404).
            ServiceUnavailableError: the briefing model or one of its data
                sources is unavailable (503).
        """

        spec = _flight_spec(
            origin=origin,
            destination=destination,
            include_weather=include_weather,
            include_notams=include_notams,
            include_pireps=include_pireps,
            format=format,
        )
        result: Any = await self._client.execute(spec, request_options)
        if format == "json":
            return cast(FlightBriefing, result)
        return _unwrap_text(result)

    async def pdf(
        self,
        *,
        departure_icao: str,
        arrival_icao: str,
        flight_number: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> bytes:
        """Briefing as a printable PDF — ``GET /briefing/pdf``.

        Args:
            departure_icao: Departure ICAO code.
            arrival_icao: Arrival ICAO code; must differ from the departure.
            flight_number: Optional, printed in the document header (``"BA117"``).

        Returns:
            The PDF as raw ``bytes`` — the body starts with the ``%PDF`` magic
            bytes. Nothing is streamed; the API buffers the whole document.

        Raises:
            UnprocessableEntityError: identical or malformed airport codes (422).
            InternalServerError: PDF rendering failed (500).
            ServiceUnavailableError: an underlying data source is down (503).
        """

        spec = _pdf_spec(
            departure_icao=departure_icao,
            arrival_icao=arrival_icao,
            flight_number=flight_number,
        )
        result: Any = await self._client.execute(spec, request_options)
        return cast(bytes, result)
