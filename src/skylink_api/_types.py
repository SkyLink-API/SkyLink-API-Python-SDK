"""Shared type aliases and the request description passed around internally."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, TypeAlias, TypedDict, TypeVar

import httpx

__all__ = [
    "NOT_GIVEN",
    "BBox",
    "BBoxLike",
    "DateLike",
    "HistoryPlan",
    "NotGiven",
    "NotGivenOr",
    "Provider",
    "Query",
    "RequestOptions",
    "RequestSpec",
    "ResponseKind",
]

_T = TypeVar("_T")

#: Delivery channel. ``direct`` talks to data.skylinkapi.com, ``rapidapi`` to the
#: RapidAPI edge (different base URL and auth headers).
Provider = Literal["direct", "rapidapi"]

#: History data plan — decides the URL prefix (``/ultra/history`` vs ``/mega/history``)
#: and therefore the allowed time window and row limits.
HistoryPlan = Literal["ultra", "mega"]

#: How the raw HTTP response should be turned into a Python value.
ResponseKind = Literal["json", "text", "bytes", "none"]

#: Bounding box as ``(lat1, lon1, lat2, lon2)`` — south-west corner first.
#: On the wire it is a single comma separated string.
BBox = tuple[float, float, float, float]
BBoxLike = str | BBox | Sequence[float]

#: Anything accepted where the API wants a date; ``str`` is passed through when it
#: is already in the endpoint's format.
DateLike = str | date | datetime

Query = Mapping[str, Any]
Headers = Mapping[str, str]


class NotGiven:
    """Sentinel for "argument omitted" where ``None`` is a meaningful value.

    ``timeout=None`` disables the timeout, so the default cannot be ``None``::

        def request(*, timeout: NotGivenOr[float | None] = NOT_GIVEN) -> None: ...
    """

    __slots__ = ()

    def __bool__(self) -> Literal[False]:
        return False

    def __repr__(self) -> str:
        return "NOT_GIVEN"


NOT_GIVEN = NotGiven()

NotGivenOr: TypeAlias = _T | NotGiven


@dataclass(frozen=True)
class RequestSpec:
    """A single HTTP call, fully described and transport agnostic.

    Resource modules build these with pure functions so that the sync and async
    clients can share every builder.
    """

    method: str
    path: str
    query: Mapping[str, Any] | None = None
    json_body: Any | None = None
    headers: Mapping[str, str] | None = None
    response_kind: ResponseKind = "json"
    #: Pydantic model (or any type pydantic can validate, e.g. ``list[Airline]``)
    #: the JSON payload is coerced into. ``None`` returns the parsed JSON as-is.
    cast_to: Any = None
    #: Dotted operation name (``"weather.metar"``) used to look up a cache TTL.
    #: Optional: when it is ``None`` the name is derived from ``path`` by
    #: :func:`skylink_api.helpers.cache.operation_name`, which is right for every
    #: endpoint the SDK models. Set it when the path cannot speak for itself.
    operation: str | None = None
    #: Endpoint specific timeout **default**, for the handful of routes the
    #: client-wide default is wrong for. It loses to an explicit
    #: ``request_options={"timeout": ...}``, so a caller can always override it;
    #: it only replaces :data:`~skylink_api._constants.DEFAULT_TIMEOUT` when the
    #: caller said nothing. ``NOT_GIVEN`` (the default) means "use the client's".
    #:
    #: Only ``/briefing/*`` sets it — see
    #: :data:`~skylink_api._constants.BRIEFING_TIMEOUT`.
    timeout: NotGivenOr[float | httpx.Timeout | None] = NOT_GIVEN


class RequestOptions(TypedDict, total=False):
    """Per-call overrides accepted by every resource method."""

    headers: Mapping[str, str]
    query: Mapping[str, Any]
    timeout: float | httpx.Timeout | None
    max_retries: int
