"""Turn the API's HTTP-200 "sentinel" answers into exceptions, on demand.

Three endpoints answer ``200`` with a payload that means "nothing found":

* ``aircraft.by_registration`` / ``by_icao24`` → ``{"found": false, "aircraft": null}``
* ``history.*`` → ``{"count": 0, "flights": [], "note": "..."}``
* ``airports.search/ip`` → ``{"error": "...", "location": null, "airports": []}``

The SDK models them as data rather than errors, deliberately: they are normal,
frequent answers and a ``try/except`` around every lookup would be noise. But in
a pipeline where a miss *is* fatal, the check-then-unwrap dance is pure
boilerplate — that is what these helpers remove. They also keep the sentinel's
own wording (``note``, ``error``) in the exception message, so the reason does
not get lost in translation.

Nothing here performs I/O; they only inspect a value you already have.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol, TypeGuard, TypeVar, runtime_checkable

from .._exceptions import NotFoundError, SkyLinkError
from ..models.aircraft import AircraftDetails, AircraftLookup
from ..models.airports import AirportsByIPResponse
from .units import _attr

__all__ = [
    "AircraftFound",
    "has_results",
    "is_found",
    "require_found",
    "require_ip_result",
    "require_results",
]


@runtime_checkable
class AircraftFound(Protocol):
    """An :class:`~skylink_api.AircraftLookup` known to be a hit.

    A structural view rather than a subclass: it narrows ``aircraft`` from
    ``AircraftDetails | None`` to ``AircraftDetails`` so type checkers stop
    demanding a ``None`` check, while the value handed back is the very same
    object the client returned.
    """

    @property
    def query(self) -> str | None: ...

    @property
    def found(self) -> bool: ...

    @property
    def aircraft(self) -> AircraftDetails: ...


def is_found(lookup: AircraftLookup) -> TypeGuard[AircraftFound]:
    """Type guard for a successful registry lookup.

    ``found`` and ``aircraft`` are checked together — the backend sets both, but
    only ``aircraft is not None`` makes the narrowed type honest::

        if is_found(result):
            print(result.aircraft.type_name)   # no Optional here
    """

    return lookup.found and lookup.aircraft is not None


def require_found(lookup: AircraftLookup) -> AircraftFound:
    """Return the lookup narrowed to a hit, or raise :class:`NotFoundError`.

    The registry endpoints never 404: an unknown registration comes back as
    ``200 {"found": false}``. Use this when a miss should stop the pipeline::

        details = require_found(sky.aircraft.by_registration("G-STBA")).aircraft

    The message quotes the identifier the API echoed back in ``query``.
    """

    if is_found(lookup):
        return lookup
    identifier = lookup.query or "<unknown>"
    raise NotFoundError(f"Aircraft {identifier!r} is not in the registry (found=false)")


def _result_lists(response: object) -> list[object]:
    """The result collections a history response may carry."""

    items: list[object] = []
    for name in ("flights", "positions", "airports", "results"):
        value = _attr(response, name)
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
            items.extend(value)
    return items


_ResponseT = TypeVar("_ResponseT")


def has_results(response: object) -> bool:
    """Whether a history-style response actually carries rows.

    Looks at the result lists first and only falls back to ``count``: the two
    always agree, but a response built by hand in a test may only set one.
    """

    if _result_lists(response):
        return True
    count = _attr(response, "count")
    return isinstance(count, int) and count > 0


def require_results(response: _ResponseT) -> _ResponseT:
    """Return the response unchanged, or raise :class:`NotFoundError` when empty.

    ``history.flights()`` answers ``200`` with ``count=0`` and an explanatory
    ``note`` when the registration is not in the aircraft database — a very
    different thing from "this aircraft never flew", and easy to miss. The note
    is quoted verbatim in the exception message when present.
    """

    if has_results(response):
        return response
    note = _attr(response, "note", "message")
    if isinstance(note, str) and note.strip():
        raise NotFoundError(f"No results: {note.strip()}")
    raise NotFoundError("No results (count=0)")


def require_ip_result(response: AirportsByIPResponse) -> AirportsByIPResponse:
    """Return the IP search unchanged, or raise :class:`SkyLinkError` on failure.

    ``airports.search/ip`` reports a geolocation failure **inside a 200**: the
    ``error`` field is set, ``location`` is ``None`` and ``airports`` is empty.
    Nothing about the HTTP layer says anything went wrong, so an unguarded caller
    just sees "no airports near me".
    """

    error = _attr(response, "error")
    if isinstance(error, str) and error.strip():
        address = _attr(response, "ip_address")
        target = f" for {address}" if isinstance(address, str) and address else ""
        raise SkyLinkError(f"IP geolocation failed{target}: {error.strip()}")
    return response
