"""``sky.compose`` — one call where an application would make seven.

Nothing in the API is aggregated: an "airport page" is a search, a METAR, a TAF,
NOTAMs, delays, charts and two schedule boards, and a "flight page" is a status
plus an airframe lookup plus a route plus an emissions estimate. Written by hand
that fan-out is always the same code, and it always gets the same two things
wrong: the requests go out one after another, and one unlucky 404 (charts are
missing, the airport is not an FAA field) takes the whole page down. This
namespace is that code, done once::

    brief = sky.compose.airport_brief("EGLL")
    print(brief.metar.raw if brief.metar else "no observation")
    print(brief.errors)        # {'delays': NotFoundError(...)} — EGLL is not FAA

Rules that hold for every method here:

* **Degrade, do not fail.** A part that errors is ``None`` in the result and its
  :class:`~skylink_api.SkyLinkError` is filed in ``errors`` under the part's own
  name. The single exception is the *primary* request, the one without which the
  aggregate means nothing — ``airports.search`` for
  :meth:`Compose.airport_brief`, the flight status for
  :meth:`Compose.flight_brief` — which is raised.
* **Only API errors are collected.** A ``TypeError`` from a bug in this SDK
  propagates; hiding it under a part name would turn a defect into "the backend
  was flaky".
* **Everything that can go out in parallel does.** The blocking classes use the
  same bounded thread pool as ``sky.batch``
  (:func:`~skylink_api.helpers.batch.map_concurrent`), the async ones use
  :func:`asyncio.gather`. A brief costs the latency of its slowest part, not the
  sum of all of them.
* **``include``/``exclude`` decide what is requested at all**, so an unwanted
  part costs no quota. The part names are exactly the field names of the result
  and are exported as :data:`AIRPORT_BRIEF_PARTS`, :data:`FLIGHT_BRIEF_PARTS`
  and :data:`ROUTE_BRIEF_PARTS`. Passing both is a :class:`ValueError`, and so
  is an unknown name — with the valid ones listed in the message.

Structure follows :mod:`skylink_api.resources.weather`: the request builders of
the underlying namespaces are reused verbatim (no endpoint knowledge is
duplicated here), the pure helpers are module-level functions shared by both
classes, and ``Compose``/``AsyncCompose`` stay method-for-method identical.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any, cast

from .._exceptions import SkyLinkError
from .._types import RequestOptions, RequestSpec
from ..helpers.batch import DEFAULT_CONCURRENCY, amap_concurrent, map_concurrent
from ..helpers.idents import classify_airport_code, normalize_icao24, normalize_registration
from ..models.adsb import AdsbAircraft
from ..models.aircraft import AircraftLookup
from ..models.compose import (
    AirportBrief,
    EnrichedAircraft,
    FlightBrief,
    RouteBrief,
    ScheduleWithStatus,
)
from ..models.flight_status import FlightStatusResponse
from ..models.geo import CountriesResponse, Country
from ..models.routes import AirlineRoutesResult, VrsRouteResult
from ..models.schedules import ArrivalsResponse, DeparturesResponse, ScheduleResponse
from .aircraft import _by_icao24_spec, _by_registration_spec
from .airports import _search_spec as _airport_search_spec
from .carbon import _estimate_spec as _carbon_estimate_spec
from .charts import _by_airport_spec as _charts_spec
from .delays import _faa_spec
from .distance import _calculate_spec as _distance_spec
from .flight_status import _flight_status_spec
from .geo import _countries_spec
from .ml import _flight_time_spec
from .notams import _by_airport_spec as _notams_spec
from .routes import _by_callsign_spec
from .schedules import ScheduleDirection, _schedule_spec
from .weather import _metar_spec, _taf_spec

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = [
    "AIRPORT_BRIEF_PARTS",
    "DEFAULT_MAX_LOOKUPS",
    "FLIGHT_BRIEF_PARTS",
    "ROUTE_BRIEF_PARTS",
    "AsyncCompose",
    "Compose",
]

#: Parts of :meth:`Compose.airport_brief`, in request order. Each name is a field
#: of :class:`~skylink_api.models.compose.AirportBrief`; pass any subset as
#: ``include=`` or ``exclude=``.
#:
#: ``"airport"`` is the primary part: while it is requested its failure is
#: raised, and excluding it simply skips the request (the other parts do not
#: depend on it — they all address the airport by the ICAO code you passed).
AIRPORT_BRIEF_PARTS: tuple[str, ...] = (
    "airport",
    "metar",
    "taf",
    "notams",
    "delays",
    "charts",
    "departures",
    "arrivals",
)

#: Parts of :meth:`Compose.flight_brief`, in dependency order — this brief is a
#: chain: ``status`` names the flight, ``route`` resolves its callsign, and
#: ``carbon`` prices the route. ``"status"`` is the primary part; excluding it
#: leaves the rest with nothing to work from.
FLIGHT_BRIEF_PARTS: tuple[str, ...] = ("status", "aircraft", "route", "carbon")

#: Parts of :meth:`Compose.route_brief`. All independent, all requested at once,
#: none primary — a route brief with one missing part is still useful.
ROUTE_BRIEF_PARTS: tuple[str, ...] = (
    "distance",
    "flight_time",
    "origin_metar",
    "destination_metar",
    "origin_taf",
    "destination_taf",
    "carbon",
)

#: How many distinct airframes :meth:`Compose.enrich_adsb` will look up in one
#: call. A bbox over Europe returns thousands of aircraft; without a ceiling a
#: single innocent-looking call would spend a day's quota.
DEFAULT_MAX_LOOKUPS = 50

#: Keys the SDK looks in for a tail number on a flight status payload. The
#: response model declares none of them — see :func:`_registration_of`.
_REGISTRATION_KEYS: tuple[str, ...] = (
    "registration",
    "aircraft_registration",
    "tail_number",
    "tail",
    "reg",
)

#: "No data", written as data. ``format_flightera_output`` in the backend's
#: scraper fills every empty text field with ``"--"`` (and other code paths use
#: ``"N/A"``), so a registration key can be present and still mean nothing. These
#: are compared after :func:`~skylink_api.helpers.idents.normalize_registration`,
#: i.e. trimmed and upper-cased; runs of dashes are matched separately.
_REGISTRATION_PLACEHOLDERS = frozenset({"", "N/A", "NA", "NONE", "UNKNOWN", "."})


# ── part selection ───────────────────────────────────────────────────────────


def _select_parts(
    known: tuple[str, ...],
    include: Iterable[str] | None,
    exclude: Iterable[str] | None,
    *,
    caller: str,
) -> tuple[str, ...]:
    """Resolve ``include``/``exclude`` into the parts to request, in canonical order.

    ``include`` is a whitelist, ``exclude`` subtracts from the full set, and
    passing both is refused rather than guessed at. Unknown names are refused
    too — a typo that silently fetched nothing would look exactly like an
    endpoint being down.

    Raises:
        ValueError: both arguments given, or a name outside ``known``.
    """

    if include is not None and exclude is not None:
        raise ValueError(f"{caller} takes include= or exclude=, not both")

    valid = ", ".join(known)
    if include is not None:
        wanted = list(dict.fromkeys(include))
        unknown = [name for name in wanted if name not in known]
        if unknown:
            raise ValueError(f"{caller}: unknown part(s) {unknown}; valid parts are {valid}")
        return tuple(name for name in known if name in set(wanted))

    if exclude is not None:
        dropped = list(dict.fromkeys(exclude))
        unknown = [name for name in dropped if name not in known]
        if unknown:
            raise ValueError(f"{caller}: unknown part(s) {unknown}; valid parts are {valid}")
        return tuple(name for name in known if name not in set(dropped))

    return known


def _check_count(value: object, name: str) -> int:
    """Validate a caller supplied row count (0 allowed), before any request.

    A **whole** number is required, matching the TypeScript SDK's ``requireCount``:
    ``limit=1.5`` is a mistake, and silently truncating it to ``1`` would hide the
    mistake behind a plausible answer. ``bool`` is rejected for the same reason
    (``True`` is an ``int`` in Python, but nobody means "one row" by it).
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer >= 0, got {value!r}")
    if value < 0:
        raise ValueError(f"{name} must be an integer >= 0, got {value!r}")
    return value


def _check_concurrency(concurrency: int) -> None:
    """Same rule (and message) as :func:`~skylink_api.helpers.batch.map_concurrent`.

    Repeated here so a method that makes a request *before* fanning out —
    :meth:`Compose.schedules_with_status` fetches the board first — still refuses
    a useless ``concurrency`` without spending that request.
    """

    if concurrency < 1:
        raise ValueError(f"concurrency must be at least 1, got {concurrency}")


# ── builders ─────────────────────────────────────────────────────────────────


def _airport_brief_specs(icao: str, *, parts: Sequence[str]) -> dict[str, RequestSpec]:
    """The requests of :meth:`Compose.airport_brief`, keyed by part name.

    Weather is asked for with ``parsed=True``: a brief exists to be read, and the
    decoded block is what :func:`skylink_api.helpers.weather.flight_category`
    and friends need.
    """

    builders: dict[str, Callable[[], RequestSpec]] = {
        "airport": lambda: _airport_search_spec(icao=icao),
        "metar": lambda: _metar_spec(icao, parsed=True),
        "taf": lambda: _taf_spec(icao, parsed=True),
        "notams": lambda: _notams_spec(icao),
        "delays": lambda: _faa_spec(icao),
        "charts": lambda: _charts_spec(icao),
        "departures": lambda: _schedule_spec("departures", icao=icao),
        "arrivals": lambda: _schedule_spec("arrivals", icao=icao),
    }
    return {name: builders[name]() for name in parts}


def _route_brief_specs(
    origin: str,
    destination: str,
    *,
    parts: Sequence[str],
    aircraft_type: str | None,
    passengers: int | None,
) -> dict[str, RequestSpec]:
    """The requests of :meth:`Compose.route_brief`, keyed by part name."""

    builders: dict[str, Callable[[], RequestSpec]] = {
        "distance": lambda: _distance_spec(from_icao=origin, to_icao=destination),
        "flight_time": lambda: _flight_time_spec(
            origin=origin, destination=destination, aircraft=aircraft_type
        ),
        "origin_metar": lambda: _metar_spec(origin, parsed=True),
        "destination_metar": lambda: _metar_spec(destination, parsed=True),
        "origin_taf": lambda: _taf_spec(origin, parsed=True),
        "destination_taf": lambda: _taf_spec(destination, parsed=True),
        "carbon": lambda: _carbon_estimate_spec(
            departure_icao=origin,
            arrival_icao=destination,
            aircraft_type=aircraft_type,
            passengers=passengers,
        ),
    }
    return {name: builders[name]() for name in parts}


def _board_spec(icao: str, direction: ScheduleDirection) -> RequestSpec:
    """``GET /schedules/{direction}`` for a code of either flavour.

    The endpoint takes ``icao=`` **or** ``iata=``, never "code", so the parameter
    is chosen by shape — three letters is IATA. This is the only part of
    :meth:`Compose.schedules_with_status` that touches the airport code, which is
    why it can be more forgiving than :meth:`Compose.airport_brief` (where METAR,
    NOTAMs and charts are all ICAO-only).
    """

    if classify_airport_code(icao) == "iata":
        return _schedule_spec(direction, iata=icao)
    return _schedule_spec(direction, icao=icao)


# ── pure helpers ─────────────────────────────────────────────────────────────


def _split(
    keys: Sequence[str],
    outcomes: Sequence[Any],
) -> tuple[dict[str, Any], dict[str, SkyLinkError]]:
    """Zip part names onto results, separating values from collectable errors.

    A non-:class:`SkyLinkError` exception is re-raised (first one, in part
    order): it is a defect in this SDK or in the caller's code, not an
    unavailable part.
    """

    values: dict[str, Any] = {}
    errors: dict[str, SkyLinkError] = {}
    for key, outcome in zip(keys, outcomes, strict=True):
        if isinstance(outcome, SkyLinkError):
            errors[key] = outcome
        elif isinstance(outcome, BaseException):
            raise outcome
        else:
            values[key] = outcome
    return values, errors


def _unique(items: Iterable[str]) -> list[str]:
    """Input order, duplicates removed — one request per distinct identifier."""

    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _truncate_board(board: Any, limit: int) -> Any:
    """Keep the first ``limit`` rows of a schedule board, envelope intact.

    ``/schedules/{direction}`` has no ``limit`` parameter — it returns everything
    it scraped (85 rows for a medium airport, several hundred for a hub) — so the
    cut happens here. ``total_flights`` is deliberately **not** rewritten: it is
    the count the API reported, and keeping it makes "there is more where this
    came from" visible.
    """

    if board is None:
        return None
    response = cast(ScheduleResponse, board)
    flights = getattr(response, "flights", [])
    return response.model_copy(update={"flights": list(flights)[:limit]})


def _normalize_flight_number(value: str | None) -> str | None:
    """``"BA 123"`` → ``"BA123"``; blanks and the scraper's dashes → ``None``.

    Flight numbers arrive spaced on the status payload (``"BA 123"``) and bare on
    schedule rows (``"C84093"``); both endpoints want them unspaced.
    """

    if not value:
        return None
    compact = "".join(value.split()).upper()
    if not compact or set(compact) <= {"-", "."}:
        return None
    return compact


def _brief_callsign(status: FlightStatusResponse | None, flight_number: str) -> str | None:
    """The designator :meth:`Compose.flight_brief` resolves the route by.

    **What the caller passed wins**, and the status payload's own flight number
    is only the fallback. That order matters, and it is the opposite of what
    looks natural: ``/flight_status`` normalises the designator to its IATA form
    (``"BAW117"`` in, ``"BA117"`` out), while ``/routes/callsign`` only finds the
    exact VRS match — the one with the departure/arrival pair that also prices
    the CO₂ — under the **ICAO** callsign. Preferring the payload therefore
    downgraded every ICAO callsign to the airline-level fallback and made the
    documented "pass ``BAW117`` for the exact match" impossible (verified live,
    2026-08-12).
    """

    return _normalize_flight_number(flight_number) or (
        _normalize_flight_number(status.flight_number) if status is not None else None
    )


def _real_registration(value: object) -> str | None:
    """Normalise a candidate tail number, rejecting the scraper's placeholders.

    ``"--"``, ``"-"``, ``"N/A"`` and ``"unknown"`` are how the upstream writes
    "this field is empty" (see ``format_flightera_output`` in the backend), and
    taking one of them at face value costs a guaranteed-404 registry lookup.
    """

    if not isinstance(value, str):
        return None
    normalized = normalize_registration(value)
    if normalized is None:
        return None
    if normalized in _REGISTRATION_PLACEHOLDERS or set(normalized) <= {"-", "."}:
        return None
    return normalized


def _registration_of(status: FlightStatusResponse) -> str | None:
    """Dig a tail number out of a flight status payload, if there is one.

    There is usually **not**. The status is built from the source page's
    ``schema.org/Flight`` JSON-LD, which carries the flight number, the airline,
    the two airports and the times — and no airframe. The model therefore
    declares no ``registration`` field, and this function looks in
    ``model_extra`` (``extra="allow"`` keeps anything the backend adds later)
    under the spellings the API family uses, including a nested ``aircraft``
    object.

    Placeholder values are treated as absent — see :func:`_real_registration`.

    Returns the registration upper-cased, or ``None`` — which is not an error,
    just "no airframe to look up".
    """

    extra = status.model_extra or {}
    nested = extra.get("aircraft")
    sources: list[dict[str, Any]] = [extra]
    if isinstance(nested, dict):
        sources.append(nested)

    for source in sources:
        for key in _REGISTRATION_KEYS:
            registration = _real_registration(source.get(key))
            if registration is not None:
                return registration
    return None


def _aircraft_type_of(lookup: AircraftLookup | None) -> str | None:
    """The ICAO type designator of a registry lookup, when it found anything."""

    if lookup is None or not lookup.found or lookup.aircraft is None:
        return None
    icao_type = lookup.aircraft.icao_type
    return icao_type.strip().upper() if icao_type and icao_type.strip() else None


def _flight_carbon_spec(
    route: VrsRouteResult | AirlineRoutesResult | None,
    *,
    callsign: str | None,
    aircraft_type: str | None,
) -> RequestSpec | None:
    """The CO₂ request for a flight brief, or ``None`` when there is nothing to price.

    ``/carbon/estimate`` accepts an **ICAO** airport pair or a callsign, and the
    two answers are not equivalent: the pair is priced by great-circle distance,
    the callsign is resolved against VRS standing data and, when a matching
    flight exists, priced on the distance actually flown. So the pair from a
    ``"vrs"`` route is used when it is available, and the callsign is the
    fallback.

    The airline-level route variant (``source="airline_routes"``) contributes
    nothing here: its airports are IATA and belong to the airline's network, not
    to this flight.
    """

    if isinstance(route, VrsRouteResult):
        departure = (route.departure_icao or "").strip().upper()
        arrival = (route.arrival_icao or "").strip().upper()
        # Equal airports are a 422 on the endpoint; fall through to the callsign.
        if departure and arrival and departure != arrival:
            return _carbon_estimate_spec(
                departure_icao=departure,
                arrival_icao=arrival,
                aircraft_type=aircraft_type,
            )
    if callsign:
        return _carbon_estimate_spec(callsign=callsign, aircraft_type=aircraft_type)
    return None


def _is_north_american(country: Country) -> bool:
    """Is this row North America? Tolerant of every spelling the API has used.

    See :meth:`Compose.north_america_countries`. Three spellings count:

    * ``"NA"`` — what the backend sends today, and the correct value;
    * ``None`` — what it sent until the 2026-08 release, when the reference CSV
      was still read with pandas' default ``NA``-as-*not-a-number* coercion;
    * ``""`` — the other shape a fixed CSV loader could produce.

    Keeping the two historical spellings costs nothing and means the method
    answers correctly against an older deployment instead of returning ``[]``.
    """

    continent = country.continent
    if continent is None:
        return True
    stripped = continent.strip()
    return not stripped or stripped.upper() == "NA"


def _lookup_plan(
    states: Sequence[AdsbAircraft],
    *,
    max_lookups: int,
) -> tuple[list[str | None], list[str]]:
    """Split ADS-B rows into "the address to look up" and "the addresses to request".

    Returns one key per state (``None`` when the aircraft broadcasts no usable
    ``icao24``) plus the distinct keys that fit in the ``max_lookups`` budget.
    Addresses are lower-cased by :func:`~skylink_api.helpers.idents.normalize_icao24`
    because responses upper-case them while the lookup endpoint takes them either
    way — without that, one airframe seen in two spellings would be two requests.
    """

    keys = [normalize_icao24(state.icao24) for state in states]
    wanted = _unique([key for key in keys if key])
    return keys, wanted[:max_lookups]


# ── sync ─────────────────────────────────────────────────────────────────────


class Compose:
    """``sky.compose`` — multi-endpoint aggregates, in parallel, failure tolerant.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            brief = sky.compose.airport_brief("KJFK", exclude=("charts", "arrivals"))
            print(brief.airport.name if brief.airport else "?", brief.errors)

    The blocking version fans out over a thread pool; the client's
    :class:`httpx.Client` is thread safe and shared, so no second connection pool
    is created.
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    # -- plumbing ------------------------------------------------------------

    def _gather(
        self,
        specs: dict[str, RequestSpec],
        *,
        request_options: RequestOptions | None,
    ) -> tuple[dict[str, Any], dict[str, SkyLinkError]]:
        """Run every spec in parallel; return ``(values, errors)`` keyed by part.

        The concurrency is the number of parts — a brief is at most eight
        requests, all to different endpoints, so there is nothing to stagger.
        The per-identifier fan-outs (``enrich_adsb``, ``schedules_with_status``)
        are the ones that take a ``concurrency`` argument, because those hit the
        *same* endpoint N times.
        """

        keys = list(specs)
        if not keys:
            return {}, {}

        def call(spec: RequestSpec) -> Any:
            return self._client.execute(spec, request_options)

        outcomes = map_concurrent(call, [specs[key] for key in keys], concurrency=len(keys))
        return _split(keys, outcomes)

    # -- aggregates ----------------------------------------------------------

    def airport_brief(
        self,
        icao: str,
        *,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
        schedules_limit: int = 10,
        request_options: RequestOptions | None = None,
    ) -> AirportBrief:
        """Everything about one airport, in one call and one round trip.

        Eight endpoints — ``airports/search``, ``weather/metar``, ``weather/taf``,
        ``notams``, ``delays/faa``, ``charts``, ``schedules/departures``,
        ``schedules/arrivals`` — fetched **in parallel**, with each failure kept
        next to the parts that worked::

            brief = sky.compose.airport_brief("EGLL", exclude=("charts",))
            if brief.metar and brief.metar.parsed:
                print(flight_category(brief.metar))
            for part, error in brief.errors.items():
                print(part, "unavailable:", error)

        Args:
            icao: **ICAO** code (``"EGLL"``). Not IATA: METAR, TAF, NOTAMs and
                charts are ICAO-only endpoints, so a three-letter code would fail
                nearly every part.
            include: Only these parts are requested. Names are the fields of
                :class:`~skylink_api.models.compose.AirportBrief`; see
                :data:`AIRPORT_BRIEF_PARTS`.
            exclude: These parts are skipped, everything else is requested.
                Mutually exclusive with ``include``.
            schedules_limit: Rows kept from each schedule board. The endpoint has
                no limit of its own and happily returns hundreds of rows;
                ``total_flights`` still reports the untruncated count. ``0``
                keeps the envelope and drops every row.
            request_options: Per-request overrides applied to **every** part.

        Returns:
            :class:`~skylink_api.models.compose.AirportBrief`.

        Note:
            ``delays`` is FAA data: for a non-US airport that part fails with a
            404 that means "not an FAA field". Exclude it outside the US.

            Weather is fetched with ``parsed=True``, so ``brief.metar`` is a
            :class:`~skylink_api.models.weather.MetarWithParsed`.

        Raises:
            ValueError: ``include`` and ``exclude`` both given, an unknown part
                name, or a negative ``schedules_limit`` — before any request.
            SkyLinkError: whatever ``airports/search`` failed with, when the
                ``airport`` part is requested. Every other failure is collected
                in ``errors`` instead.
        """

        parts = _select_parts(
            AIRPORT_BRIEF_PARTS, include, exclude, caller="compose.airport_brief()"
        )
        limit = _check_count(schedules_limit, "schedules_limit")
        specs = _airport_brief_specs(icao, parts=parts)
        values, errors = self._gather(specs, request_options=request_options)

        if "airport" in errors:
            raise errors["airport"]

        return AirportBrief(
            airport=values.get("airport"),
            metar=values.get("metar"),
            taf=values.get("taf"),
            notams=values.get("notams"),
            delays=values.get("delays"),
            charts=values.get("charts"),
            departures=_truncate_board(values.get("departures"), limit),
            arrivals=_truncate_board(values.get("arrivals"), limit),
            errors=errors,
        )

    def flight_brief(
        self,
        flight_number: str,
        *,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
        request_options: RequestOptions | None = None,
    ) -> FlightBrief:
        """One flight, joined with its airframe, its route and its CO₂.

        A chain rather than a fan-out, because each step needs the previous
        one's answer::

            flight_status(number)                       # the primary request
              → registration (if the payload has one) → aircraft.registration()
              → callsign → routes.callsign()           # these two in parallel
              → the route's ICAO pair                  → carbon.estimate()

        Only the steps that genuinely depend on each other are serialised; the
        airframe lookup and the route resolution go out together.

        Args:
            flight_number: IATA (``"BA123"``) or ICAO (``"BAW123"``) designator.
            include: Only these parts are requested; see
                :data:`FLIGHT_BRIEF_PARTS`.
            exclude: These parts are skipped. Mutually exclusive with ``include``.
            request_options: Per-request overrides applied to every part.

        Returns:
            :class:`~skylink_api.models.compose.FlightBrief`.

        Note:
            **The airframe part is usually empty**, and that is not a failure:
            the live status payload carries no registration (its upstream source
            is a ``schema.org/Flight`` block that has no airframe), so there is
            nothing to look up and ``aircraft`` stays ``None`` with no entry in
            ``errors``. When a registration *is* present, a miss comes back as an
            ``AircraftLookup`` with ``found=False`` — a ``200`` sentinel, also not
            an error.

            The callsign the route is looked up by is **what you passed**, with
            spaces removed (``"BA 123"`` → ``"BA123"``), falling back to the
            status payload's own flight number. An IATA number resolves through
            the airline-level fallback, so ``route.source`` is often
            ``"airline_routes"``; pass the ICAO callsign (``"BAW117"``) for the
            exact ``"vrs"`` match — and with it the airport pair that prices the
            CO₂. The payload cannot be preferred here: ``/flight_status`` echoes
            the designator in its IATA form, which would erase that choice.

        Raises:
            ValueError: ``include`` and ``exclude`` both given, or an unknown
                part name — before any request.
            SkyLinkError: whatever ``flight_status`` failed with, when the
                ``status`` part is requested.
        """

        parts = _select_parts(FLIGHT_BRIEF_PARTS, include, exclude, caller="compose.flight_brief()")
        errors: dict[str, SkyLinkError] = {}

        status: FlightStatusResponse | None = None
        if "status" in parts:
            status = cast(
                FlightStatusResponse,
                self._client.execute(_flight_status_spec(flight_number), request_options),
            )

        registration = _registration_of(status) if status is not None else None
        callsign = _brief_callsign(status, flight_number)

        specs: dict[str, RequestSpec] = {}
        if "aircraft" in parts and registration:
            specs["aircraft"] = _by_registration_spec(registration)
        if "route" in parts and callsign:
            specs["route"] = _by_callsign_spec(callsign)
        values, stage_errors = self._gather(specs, request_options=request_options)
        errors.update(stage_errors)

        route = values.get("route")
        aircraft = values.get("aircraft")
        carbon = None
        if "carbon" in parts:
            carbon_spec = _flight_carbon_spec(
                route,
                callsign=callsign,
                aircraft_type=_aircraft_type_of(aircraft),
            )
            if carbon_spec is not None:
                carbon_values, carbon_errors = self._gather(
                    {"carbon": carbon_spec}, request_options=request_options
                )
                carbon = carbon_values.get("carbon")
                errors.update(carbon_errors)

        return FlightBrief(
            status=status,
            aircraft=aircraft,
            route=route,
            carbon=carbon,
            errors=errors,
        )

    def route_brief(
        self,
        origin: str,
        destination: str,
        *,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
        aircraft_type: str | None = None,
        passengers: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> RouteBrief:
        """A city pair, briefed: distance, block time, both ends' weather, CO₂.

        Seven independent requests, all in parallel, nothing primary::

            brief = sky.compose.route_brief("EGLL", "KJFK")
            print(brief.distance.distance if brief.distance else "?", "nm")
            print(brief.flight_time.estimated_hours_display if brief.flight_time else "?")

        Args:
            origin: Departure airport. ICAO (``"EGLL"``) — ``distance`` and
                ``flight_time`` also accept IATA, but METAR, TAF and carbon do
                not, so ICAO is the only code that works for the whole brief.
            destination: Arrival airport, same rule.
            include: Only these parts are requested; see
                :data:`ROUTE_BRIEF_PARTS`.
            exclude: These parts are skipped. Mutually exclusive with ``include``.
            aircraft_type: ICAO type designator (``"B77W"``) passed to the block
                time model and the CO₂ estimate. Omit to let both use their own
                defaults (a category average for carbon).
            passengers: Seats to divide the emissions over. Omit for the
                aircraft category's typical seat count.
            request_options: Per-request overrides applied to every part.

        Returns:
            :class:`~skylink_api.models.compose.RouteBrief`. Every failure is in
            ``errors``; this method does not raise for an unavailable part.

        Note:
            ``carbon`` rejects an identical origin and destination with a 422 —
            it lands in ``errors`` like any other part.

        Raises:
            ValueError: ``include`` and ``exclude`` both given, or an unknown part
                name — before any request.
        """

        parts = _select_parts(ROUTE_BRIEF_PARTS, include, exclude, caller="compose.route_brief()")
        specs = _route_brief_specs(
            origin,
            destination,
            parts=parts,
            aircraft_type=aircraft_type,
            passengers=passengers,
        )
        values, errors = self._gather(specs, request_options=request_options)

        return RouteBrief(
            distance=values.get("distance"),
            flight_time=values.get("flight_time"),
            origin_metar=values.get("origin_metar"),
            destination_metar=values.get("destination_metar"),
            origin_taf=values.get("origin_taf"),
            destination_taf=values.get("destination_taf"),
            carbon=values.get("carbon"),
            errors=errors,
        )

    def enrich_adsb(
        self,
        states: Iterable[AdsbAircraft],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        max_lookups: int = DEFAULT_MAX_LOOKUPS,
        photos: bool = False,
        request_options: RequestOptions | None = None,
    ) -> list[EnrichedAircraft]:
        """Join live ADS-B contacts with the airframe registry.

        The feed knows where an aircraft is; the registry knows what it is. This
        pairs them up, one lookup per **distinct** ``icao24``::

            page = sky.adsb.aircraft(bbox=(51.0, -1.0, 52.0, 0.5), limit=20)
            for row in sky.compose.enrich_adsb(page.aircraft):
                kind = row.info.aircraft.type_name if row.info and row.info.aircraft else "?"
                print(row.state.callsign, kind)

        Args:
            states: ADS-B rows, typically ``sky.adsb.aircraft(...).aircraft``.
            concurrency: Simultaneous lookups, default 5 (RapidAPI quotas).
            max_lookups: Ceiling on **distinct** addresses looked up in this call,
                default 50. Aircraft beyond it get ``info=None`` **without a
                request** — no error, just no data; the feed routinely returns
                thousands of rows and this is what stops one call from spending a
                day's quota. Pass ``0`` to make the join a no-op.
            photos: Fetch photo URLs with each lookup. **Off by default here**,
                unlike ``sky.aircraft.by_icao24()``, because it is the slow part
                of that endpoint and this method makes up to ``max_lookups`` calls.
            request_options: Per-request overrides applied to every lookup.

        Returns:
            One :class:`~skylink_api.models.compose.EnrichedAircraft` per input
            state, in input order. A repeated address is requested once and the
            answer is shared, so the returned objects may hold the same ``info``.

        Raises:
            ValueError: ``concurrency`` below 1 or a negative ``max_lookups`` —
                before any request.
        """

        rows = list(states)
        budget = _check_count(max_lookups, "max_lookups")
        keys, wanted = _lookup_plan(rows, max_lookups=budget)

        def call(key: str) -> Any:
            return self._client.execute(_by_icao24_spec(key, photos=photos), request_options)

        outcomes = map_concurrent(call, wanted, concurrency=concurrency)
        found, failed = _split(wanted, outcomes)
        return _build_enriched(rows, keys, found, failed)

    def schedules_with_status(
        self,
        icao: str,
        *,
        direction: ScheduleDirection = "departures",
        limit: int = 10,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_options: RequestOptions | None = None,
    ) -> list[ScheduleWithStatus]:
        """A departure or arrival board, with the live status of each flight.

        The board is scraped prose (``"Estimated 16:39"``); the status endpoint
        knows gates, terminals and actual times. This fetches the board, then the
        status of its first ``limit`` rows in parallel::

            for row in sky.compose.schedules_with_status("EGLL", limit=5):
                print(row.entry.time, row.entry.flight, row.status.status if row.status else "-")

        Args:
            icao: Airport code. ICAO (``"EGLL"``) or IATA (``"LHR"``) — this
                method only touches ``/schedules``, which accepts both, and the
                right parameter is chosen from the code's shape.
            direction: ``"departures"`` (default) or ``"arrivals"``.
            limit: Board rows to enrich, counted from the top. The endpoint
                returns everything it scraped, so without a limit this would be
                hundreds of status requests. ``0`` returns an empty list without
                a status request.
            concurrency: Simultaneous status requests, default 5.
            request_options: Per-request overrides applied to every request.

        Returns:
            One :class:`~skylink_api.models.compose.ScheduleWithStatus` per kept
            row, in board order. Duplicate flight numbers are requested once.

        Note:
            Board rows use **PascalCase** keys on the wire (``Flight``, ``Time``,
            ``IATA``); the model exposes them as ``entry.flight``, ``entry.time``
            and so on. Rows with no flight number are returned with
            ``status=None`` and no error — nothing was requested for them.

            A flight number the status endpoint cannot resolve is a 404 in
            ``row.error``; it is common for charter and cargo rows and does not
            affect the other rows.

        Raises:
            ValueError: unknown ``direction``, negative ``limit`` or
                ``concurrency`` below 1 — before any request.
            SkyLinkError: whatever the board request failed with. Without the
                board there is nothing to enrich, so that one is not collected.
        """

        rows_wanted = _check_count(limit, "limit")
        _check_concurrency(concurrency)
        board = cast(
            DeparturesResponse | ArrivalsResponse,
            self._client.execute(_board_spec(icao, _check_direction(direction)), request_options),
        )
        rows = list(board.flights)[:rows_wanted]
        numbers = _unique([number for number in map(_flight_of, rows) if number])

        def call(number: str) -> Any:
            return self._client.execute(_flight_status_spec(number), request_options)

        outcomes = map_concurrent(call, numbers, concurrency=concurrency)
        found, failed = _split(numbers, outcomes)
        return _build_schedule_rows(rows, found, failed)

    def north_america_countries(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> list[Country]:
        """The 41 North American countries, whatever the backend calls them::

            for country in sky.compose.north_america_countries():
                print(country.code, country.name)

        Args:
            request_options: Per-request overrides.

        Returns:
            Countries whose ``continent`` is ``"NA"`` — or empty, the shape older
            deployments sent — in the API's own order.

        Note:
            This began as a workaround. ``geo.countries(continent="NA")`` used to
            return nothing, because the reference CSV was read with pandas, which
            parses the literal ``NA`` as *not-a-number*; every North American
            country therefore arrived with ``continent: null`` and the
            server-side filter matched zero rows.

            **That backend bug is fixed** — as of 2026-08-15
            ``geo.countries(continent="NA")`` returns the same 41 countries in
            one small response, and it is the call to reach for. This method is
            kept because it is public API and because it still answers correctly
            against a deployment that predates the fix; the cost is that it
            downloads all ~250 countries and filters client side.

        See Also:
            :meth:`skylink_api.resources.geo.Geo.countries` — the direct route.
        """

        response = cast(CountriesResponse, self._client.execute(_countries_spec(), request_options))
        return [country for country in response.countries if _is_north_american(country)]


# ── shared result assembly ───────────────────────────────────────────────────


def _check_direction(direction: str) -> ScheduleDirection:
    """Reject a misspelled board direction before the request."""

    if direction not in ("departures", "arrivals"):
        raise ValueError(f"direction must be 'departures' or 'arrivals', got {direction!r}")
    return cast(ScheduleDirection, direction)


def _flight_of(entry: Any) -> str | None:
    """The row's flight number, unspaced — wire key ``Flight`` (PascalCase)."""

    return _normalize_flight_number(getattr(entry, "flight", None))


def _build_enriched(
    rows: Sequence[AdsbAircraft],
    keys: Sequence[str | None],
    found: dict[str, Any],
    failed: dict[str, SkyLinkError],
) -> list[EnrichedAircraft]:
    """Fan the per-address lookups back out onto the input rows."""

    enriched: list[EnrichedAircraft] = []
    for state, key in zip(rows, keys, strict=True):
        if key is None:
            enriched.append(EnrichedAircraft(state=state))
            continue
        enriched.append(EnrichedAircraft(state=state, info=found.get(key), error=failed.get(key)))
    return enriched


def _build_schedule_rows(
    rows: Sequence[Any],
    found: dict[str, Any],
    failed: dict[str, SkyLinkError],
) -> list[ScheduleWithStatus]:
    """Fan the per-number statuses back out onto the board rows."""

    joined: list[ScheduleWithStatus] = []
    for entry in rows:
        number = _flight_of(entry)
        if number is None:
            joined.append(ScheduleWithStatus(entry=entry))
            continue
        joined.append(
            ScheduleWithStatus(entry=entry, status=found.get(number), error=failed.get(number))
        )
    return joined


# ── async ────────────────────────────────────────────────────────────────────


class AsyncCompose:
    """``sky.compose`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Compose` — same parts, same names, same degradation rules;
    the fan-out is :func:`asyncio.gather` instead of a thread pool::

        async with AsyncSkyLink(api_key="...") as sky:
            brief = await sky.compose.airport_brief("EGLL")
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    # -- plumbing ------------------------------------------------------------

    async def _gather(
        self,
        specs: dict[str, RequestSpec],
        *,
        request_options: RequestOptions | None,
    ) -> tuple[dict[str, Any], dict[str, SkyLinkError]]:
        """Run every spec concurrently; return ``(values, errors)`` keyed by part."""

        keys = list(specs)
        if not keys:
            return {}, {}

        outcomes = await asyncio.gather(
            *(self._client.execute(specs[key], request_options) for key in keys),
            return_exceptions=True,
        )
        return _split(keys, outcomes)

    # -- aggregates ----------------------------------------------------------

    async def airport_brief(
        self,
        icao: str,
        *,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
        schedules_limit: int = 10,
        request_options: RequestOptions | None = None,
    ) -> AirportBrief:
        """Everything about one airport, in one call and one round trip.

        Async twin of :meth:`Compose.airport_brief`; see it for the full argument
        documentation and the degradation rules::

            brief = await sky.compose.airport_brief("EGLL", exclude=("delays",))

        Args:
            icao: **ICAO** code — most parts are ICAO-only endpoints.
            include: Only these parts are requested (:data:`AIRPORT_BRIEF_PARTS`).
            exclude: These parts are skipped; mutually exclusive with ``include``.
            schedules_limit: Rows kept from each schedule board.
            request_options: Per-request overrides applied to every part.

        Raises:
            ValueError: both selectors given, an unknown part, or a negative
                ``schedules_limit`` — before any request.
            SkyLinkError: the ``airports/search`` failure, when that part is
                requested.
        """

        parts = _select_parts(
            AIRPORT_BRIEF_PARTS, include, exclude, caller="compose.airport_brief()"
        )
        limit = _check_count(schedules_limit, "schedules_limit")
        specs = _airport_brief_specs(icao, parts=parts)
        values, errors = await self._gather(specs, request_options=request_options)

        if "airport" in errors:
            raise errors["airport"]

        return AirportBrief(
            airport=values.get("airport"),
            metar=values.get("metar"),
            taf=values.get("taf"),
            notams=values.get("notams"),
            delays=values.get("delays"),
            charts=values.get("charts"),
            departures=_truncate_board(values.get("departures"), limit),
            arrivals=_truncate_board(values.get("arrivals"), limit),
            errors=errors,
        )

    async def flight_brief(
        self,
        flight_number: str,
        *,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
        request_options: RequestOptions | None = None,
    ) -> FlightBrief:
        """One flight, joined with its airframe, its route and its CO₂.

        Async twin of :meth:`Compose.flight_brief`; see it for the chain, for why
        ``aircraft`` is usually ``None``, and for the callsign rule::

            brief = await sky.compose.flight_brief("BAW117")

        Args:
            flight_number: IATA or ICAO designator.
            include: Only these parts are requested (:data:`FLIGHT_BRIEF_PARTS`).
            exclude: These parts are skipped; mutually exclusive with ``include``.
            request_options: Per-request overrides applied to every part.

        Raises:
            ValueError: both selectors given or an unknown part — before any
                request.
            SkyLinkError: the ``flight_status`` failure, when that part is
                requested.
        """

        parts = _select_parts(FLIGHT_BRIEF_PARTS, include, exclude, caller="compose.flight_brief()")
        errors: dict[str, SkyLinkError] = {}

        status: FlightStatusResponse | None = None
        if "status" in parts:
            result: Any = await self._client.execute(
                _flight_status_spec(flight_number), request_options
            )
            status = cast(FlightStatusResponse, result)

        registration = _registration_of(status) if status is not None else None
        callsign = _brief_callsign(status, flight_number)

        specs: dict[str, RequestSpec] = {}
        if "aircraft" in parts and registration:
            specs["aircraft"] = _by_registration_spec(registration)
        if "route" in parts and callsign:
            specs["route"] = _by_callsign_spec(callsign)
        values, stage_errors = await self._gather(specs, request_options=request_options)
        errors.update(stage_errors)

        route = values.get("route")
        aircraft = values.get("aircraft")
        carbon = None
        if "carbon" in parts:
            carbon_spec = _flight_carbon_spec(
                route,
                callsign=callsign,
                aircraft_type=_aircraft_type_of(aircraft),
            )
            if carbon_spec is not None:
                carbon_values, carbon_errors = await self._gather(
                    {"carbon": carbon_spec}, request_options=request_options
                )
                carbon = carbon_values.get("carbon")
                errors.update(carbon_errors)

        return FlightBrief(
            status=status,
            aircraft=aircraft,
            route=route,
            carbon=carbon,
            errors=errors,
        )

    async def route_brief(
        self,
        origin: str,
        destination: str,
        *,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
        aircraft_type: str | None = None,
        passengers: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> RouteBrief:
        """A city pair, briefed: distance, block time, both ends' weather, CO₂.

        Async twin of :meth:`Compose.route_brief`; seven independent requests,
        nothing primary, every failure collected::

            brief = await sky.compose.route_brief("EGLL", "KJFK")

        Args:
            origin: Departure airport, ICAO.
            destination: Arrival airport, ICAO.
            include: Only these parts are requested (:data:`ROUTE_BRIEF_PARTS`).
            exclude: These parts are skipped; mutually exclusive with ``include``.
            aircraft_type: ICAO type designator for the block time and CO₂ models.
            passengers: Seats to divide the emissions over.
            request_options: Per-request overrides applied to every part.

        Raises:
            ValueError: both selectors given or an unknown part — before any
                request.
        """

        parts = _select_parts(ROUTE_BRIEF_PARTS, include, exclude, caller="compose.route_brief()")
        specs = _route_brief_specs(
            origin,
            destination,
            parts=parts,
            aircraft_type=aircraft_type,
            passengers=passengers,
        )
        values, errors = await self._gather(specs, request_options=request_options)

        return RouteBrief(
            distance=values.get("distance"),
            flight_time=values.get("flight_time"),
            origin_metar=values.get("origin_metar"),
            destination_metar=values.get("destination_metar"),
            origin_taf=values.get("origin_taf"),
            destination_taf=values.get("destination_taf"),
            carbon=values.get("carbon"),
            errors=errors,
        )

    async def enrich_adsb(
        self,
        states: Iterable[AdsbAircraft],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        max_lookups: int = DEFAULT_MAX_LOOKUPS,
        photos: bool = False,
        request_options: RequestOptions | None = None,
    ) -> list[EnrichedAircraft]:
        """Join live ADS-B contacts with the airframe registry.

        Async twin of :meth:`Compose.enrich_adsb`; same memoisation and the same
        ``max_lookups`` budget::

            page = await sky.adsb.aircraft(bbox=(51.0, -1.0, 52.0, 0.5), limit=20)
            rows = await sky.compose.enrich_adsb(page.aircraft)

        Args:
            states: ADS-B rows.
            concurrency: Simultaneous lookups, default 5.
            max_lookups: Ceiling on distinct addresses looked up, default 50;
                aircraft beyond it get ``info=None`` without a request.
            photos: Fetch photo URLs with each lookup (off by default — slow).
            request_options: Per-request overrides applied to every lookup.

        Raises:
            ValueError: ``concurrency`` below 1 or a negative ``max_lookups``.
        """

        rows = list(states)
        budget = _check_count(max_lookups, "max_lookups")
        keys, wanted = _lookup_plan(rows, max_lookups=budget)

        async def call(key: str) -> Any:
            return await self._client.execute(_by_icao24_spec(key, photos=photos), request_options)

        outcomes = await amap_concurrent(call, wanted, concurrency=concurrency)
        found, failed = _split(wanted, outcomes)
        return _build_enriched(rows, keys, found, failed)

    async def schedules_with_status(
        self,
        icao: str,
        *,
        direction: ScheduleDirection = "departures",
        limit: int = 10,
        concurrency: int = DEFAULT_CONCURRENCY,
        request_options: RequestOptions | None = None,
    ) -> list[ScheduleWithStatus]:
        """A departure or arrival board, with the live status of each flight.

        Async twin of :meth:`Compose.schedules_with_status`; see it for the
        PascalCase note and the per-row error rules::

            rows = await sky.compose.schedules_with_status("EGLL", limit=5)

        Args:
            icao: Airport code, ICAO or IATA (the parameter is chosen by shape).
            direction: ``"departures"`` (default) or ``"arrivals"``.
            limit: Board rows to enrich, counted from the top.
            concurrency: Simultaneous status requests, default 5.
            request_options: Per-request overrides applied to every request.

        Raises:
            ValueError: unknown ``direction``, negative ``limit`` or
                ``concurrency`` below 1 — before any request.
            SkyLinkError: the board request's failure.
        """

        rows_wanted = _check_count(limit, "limit")
        _check_concurrency(concurrency)
        result: Any = await self._client.execute(
            _board_spec(icao, _check_direction(direction)), request_options
        )
        board = cast("DeparturesResponse | ArrivalsResponse", result)
        rows = list(board.flights)[:rows_wanted]
        numbers = _unique([number for number in map(_flight_of, rows) if number])

        async def call(number: str) -> Any:
            return await self._client.execute(_flight_status_spec(number), request_options)

        outcomes = await amap_concurrent(call, numbers, concurrency=concurrency)
        found, failed = _split(numbers, outcomes)
        return _build_schedule_rows(rows, found, failed)

    async def north_america_countries(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> list[Country]:
        """The 41 North American countries, whatever the backend calls them.

        Async twin of :meth:`Compose.north_america_countries` — including its
        note that the backend bug this once worked around is fixed, and that
        ``geo.countries(continent="NA")`` is now the cheaper route::

            countries = await sky.compose.north_america_countries()

        Args:
            request_options: Per-request overrides.
        """

        result: Any = await self._client.execute(_countries_spec(), request_options)
        response = cast(CountriesResponse, result)
        return [country for country in response.countries if _is_north_american(country)]
