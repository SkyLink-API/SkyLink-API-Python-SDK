"""Aviation identifier classification and normalisation.

Every endpoint in the API keys off one of four identifier families — airport
codes, transponder addresses, registrations and flight numbers — and each has a
trap:

* ``airports.search`` autodetects IATA vs ICAO, but you often need to know
  *before* the call (to pick a parameter, or to route a user's input).
* ``airports.search/location`` and ``/ip`` return **OurAirports pseudo-codes**
  (``GB-0888``) that ``airports.search`` cannot resolve — joining on them
  silently produces 404s, so :func:`is_local_pseudocode` filters them out first.
* ``history.positions()`` dispatches on "is this 6 hex or a registration";
  :func:`is_icao24` is that test, made public.
* Callsigns and flight numbers look alike but are not: ``BA1403`` (IATA) and
  ``BAW175`` (ICAO callsign) address the same airline in different alphabets.

All functions are pure and never raise; unusable input yields ``"unknown"``,
``False`` or ``None``.
"""

from __future__ import annotations

import re
from typing import Literal, NamedTuple

__all__ = [
    "AirportCodeKind",
    "FlightNumber",
    "classify_airport_code",
    "is_icao24",
    "is_local_pseudocode",
    "normalize_icao24",
    "normalize_registration",
    "split_flight_number",
]

#: What :func:`classify_airport_code` decided a string is.
AirportCodeKind = Literal["iata", "icao", "local", "unknown"]

_IATA_CODE_RE = re.compile(r"^[A-Z]{3}$")
_ICAO_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{3}$")
#: OurAirports pseudo-code: an ISO country prefix and a local number (``US-0123``).
_LOCAL_CODE_RE = re.compile(r"^[A-Z]{2}-[A-Z0-9]+$")
_ICAO24_RE = re.compile(r"^[0-9A-F]{6}$", re.IGNORECASE)
#: ``BA1403``, ``U21234`` — a two-character IATA airline code plus a number.
_IATA_FLIGHT_RE = re.compile(r"^(?P<airline>[A-Z][A-Z0-9])(?P<number>\d{1,4}[A-Z]?)$")
#: ``BAW175`` — a three-letter ICAO airline code plus a number.
_ICAO_FLIGHT_RE = re.compile(r"^(?P<airline>[A-Z]{3})(?P<number>\d{1,4}[A-Z]?)$")


def _normalized(value: str | None) -> str:
    return value.strip().upper() if isinstance(value, str) else ""


def classify_airport_code(code: str | None) -> AirportCodeKind:
    """Tell an IATA code from an ICAO code from an OurAirports pseudo-code.

    * three letters (``JFK``) → ``"iata"``
    * four alphanumerics starting with a letter (``KJFK``, ``EG12``) → ``"icao"``
    * anything containing a dash (``GB-0888``) → ``"local"``
    * everything else → ``"unknown"``

    Case and surrounding whitespace do not matter. This is the same rule
    ``airports.search`` applies server-side, available before you spend a call.
    """

    code = _normalized(code)
    if not code:
        return "unknown"
    if "-" in code:
        return "local"
    if _IATA_CODE_RE.match(code):
        return "iata"
    if _ICAO_CODE_RE.match(code):
        return "icao"
    return "unknown"


def is_local_pseudocode(code: str | None) -> bool:
    """Whether ``code`` is an OurAirports pseudo-code such as ``GB-0888``.

    ``airports.search/location`` and ``/ip`` return these for small fields with
    no ICAO code, and ``airports.search`` **will not resolve them** — enriching a
    nearby-airport list without filtering them first produces a burst of 404s.
    """

    return bool(_LOCAL_CODE_RE.match(_normalized(code)))


def is_icao24(value: str | None) -> bool:
    """Whether ``value`` is a transponder address: exactly six hex digits.

    Used to tell ``4ca1d3`` from ``G-STBA`` when dispatching to the right
    endpoint. Strictly six hex characters — a tar1090-style ``~`` prefix (a
    non-ICAO address) is not one; run :func:`normalize_icao24` first if your
    source produces those.
    """

    return bool(_ICAO24_RE.match(value.strip())) if isinstance(value, str) else False


def normalize_icao24(value: str | None) -> str | None:
    """Canonicalise a transponder address to lower-case hex, or ``None``.

    Trims whitespace and a leading ``~`` (feeders mark non-ICAO addresses that
    way) and lower-cases the result, which is the form the query side of the API
    wants — responses come back **upper-cased**, so round-tripping a value from a
    response into a request without this is a common mismatch.

    Returns ``None`` when what is left is not six hex digits.
    """

    if not isinstance(value, str):
        return None
    text = value.strip().lstrip("~").strip()
    return text.lower() if _ICAO24_RE.match(text) else None


def normalize_registration(value: str | None) -> str | None:
    """Canonicalise a tail number: trimmed and upper-cased (``"g-stba"`` → ``"G-STBA"``).

    Separators are **kept** — the registry stores ``G-STBA`` with the dash and
    ``aircraft.by_registration`` matches on it. Returns ``None`` for empty input.
    """

    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).upper()
    return text or None


class FlightNumber(NamedTuple):
    """A flight designator split into its airline prefix and number."""

    airline: str
    """``"BA"`` (IATA) or ``"BAW"`` (ICAO), upper-cased."""

    number: str
    """The numeric part **verbatim** — leading zeros are meaningful in some
    schedules, so it stays a string."""

    kind: Literal["iata", "icao"]
    """Which alphabet the airline prefix is in. ``flight_status`` takes the IATA
    form; ADS-B callsigns and ``routes.callsign`` use the ICAO one."""


def split_flight_number(value: str | None) -> FlightNumber | None:
    """Split ``"BA1403"`` into ``("BA", "1403", "iata")``.

    Handles the three shapes that reach the API: IATA numbers (``BA1403``),
    two-character codes whose second character is a digit (``U21234`` →
    ``("U2", "1234")`` — a naive "letters then digits" split gets this wrong),
    and ICAO callsigns (``BAW175`` → ``("BAW", "175", "icao")``). Spaces and
    dashes are ignored, so ``"BA 1403"`` works.

    Returns ``None`` when the string is not a flight designator — including bare
    tail-number callsigns such as ``N123AB``, which carry no airline at all.
    """

    if not isinstance(value, str):
        return None
    text = re.sub(r"[\s\-/]", "", value).upper()
    if not text:
        return None

    match = _IATA_FLIGHT_RE.match(text)
    if match is not None:
        return FlightNumber(airline=match["airline"], number=match["number"], kind="iata")
    match = _ICAO_FLIGHT_RE.match(text)
    if match is not None:
        return FlightNumber(airline=match["airline"], number=match["number"], kind="icao")
    return None
