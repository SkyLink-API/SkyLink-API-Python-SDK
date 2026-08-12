"""Results assembled by the aggregators (``sky.compose``).

Like :mod:`skylink_api.models.poll`, nothing here is a payload the API sends:
each class is stitched together by the SDK from several responses, and every
one of them carries the **errors of the parts that failed** alongside the parts
that worked. That is why they are plain dataclasses rather than
:class:`~skylink_api.models._base.SkyLinkModel` subclasses — there is nothing to
parse, no unknown backend fields to keep, and a pydantic model holding
exceptions would be a category error.

The shape is the same everywhere::

    brief = sky.compose.airport_brief("EGLL")
    if brief.metar is not None:
        print(brief.metar.raw)
    for part, error in brief.errors.items():
        log.warning("%s unavailable: %s", part, error)

Rules that hold for all of them:

* **A part is ``None`` when it failed**, and the failure is in ``errors`` under
  the field's own name (``"metar"``, ``"notams"``, ...). A part is also ``None``
  when it was not requested at all (``include``/``exclude``), in which case
  ``errors`` has no entry for it — absence of data and failure to get data are
  distinguishable.
* ``errors`` only ever holds :class:`~skylink_api.SkyLinkError`. A programming
  error propagates instead of being filed as an unavailable part.
* Nothing here is truncated or merged: each field is exactly the object the
  corresponding single-endpoint method would have returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._exceptions import SkyLinkError
from .adsb import AdsbAircraft
from .aircraft import AircraftLookup
from .airports import EnrichedAirport
from .carbon import CarbonEstimate
from .charts import ChartsResponse
from .delays import FaaDelayResponse
from .distance import DistanceResponse
from .flight_status import FlightStatusResponse
from .ml import FlightTimePrediction
from .notams import NotamsResponse
from .routes import AirlineRoutesResult, VrsRouteResult
from .schedules import ArrivalsResponse, DeparturesResponse, ScheduleFlight
from .weather import MetarWithParsed, TafWithParsed

__all__ = [
    "AirportBrief",
    "EnrichedAircraft",
    "FlightBrief",
    "RouteBrief",
    "ScheduleWithStatus",
]


@dataclass
class AirportBrief:
    """Everything worth knowing about one airport right now.

    Returned by :meth:`skylink_api.resources.compose.Compose.airport_brief`,
    which fetches all eight parts in parallel. Weather is requested with
    ``parsed=True``, so :func:`skylink_api.helpers.weather.flight_category` works
    on it directly.

    Attributes:
        airport: The enriched airport record — runways, frequencies, country and
            region. The *primary* part: unless it was excluded, its failure is
            raised rather than collected.
        metar: Current observation, decoded (``parsed=True``).
        taf: Terminal forecast, decoded (``parsed=True``). Small fields often
            issue no TAF at all, so a 404 here is routine.
        notams: Active NOTAMs. An airport with none answers ``200`` with an
            empty list — that is data, not an error.
        delays: FAA NAS status. **US airports only**: everywhere else this part
            fails with a 404 that means "not an FAA field", not "no delays".
        charts: Aerodrome charts grouped by category; the map is partial by
            nature — categories with no chart are simply absent.
        departures: Departure board, with ``flights`` truncated to
            ``schedules_limit``. ``total_flights`` is the count **before**
            truncation, so it stays comparable with a direct
            ``sky.schedules.departures()`` call.
        arrivals: Arrival board, truncated the same way.
        errors: ``{part name: error}`` for the parts that failed. Empty when
            everything worked.
    """

    airport: EnrichedAirport | None = None
    metar: MetarWithParsed | None = None
    taf: TafWithParsed | None = None
    notams: NotamsResponse | None = None
    delays: FaaDelayResponse | None = None
    charts: ChartsResponse | None = None
    departures: DeparturesResponse | None = None
    arrivals: ArrivalsResponse | None = None
    errors: dict[str, SkyLinkError] = field(default_factory=dict)


@dataclass
class FlightBrief:
    """One flight, joined with its airframe, its route and its emissions.

    Returned by :meth:`skylink_api.resources.compose.Compose.flight_brief`. This
    is a **chain**, not a fan-out: the status names the flight, the route comes
    from its callsign, and the CO₂ estimate needs the route's airport pair.

    Attributes:
        status: Live status — the primary part, raised rather than collected.
        aircraft: Registry lookup of the airframe, when the status names one.

            .. warning::
               The live payload usually does **not** carry a registration (the
               upstream JSON-LD has no such field), so this is normally ``None``
               with no error: nothing was looked up. ``found=False`` from the
               lookup is likewise not an error — it is a ``200`` sentinel, and it
               is kept here as an ``AircraftLookup`` with ``aircraft=None``.
        route: Callsign resolution. Two different shapes discriminated by
            ``source``: ``"vrs"`` names this flight's airport pair (ICAO),
            ``"airline_routes"`` is the airline-level fallback and says nothing
            about *this* flight.
        carbon: CO₂ estimate for the flight, computed from the route's ICAO
            pair when the VRS variant supplied one, and from the callsign
            otherwise. ``None`` when neither was available.
        errors: ``{part name: error}`` for the parts that failed.
    """

    status: FlightStatusResponse | None = None
    aircraft: AircraftLookup | None = None
    route: VrsRouteResult | AirlineRoutesResult | None = None
    carbon: CarbonEstimate | None = None
    errors: dict[str, SkyLinkError] = field(default_factory=dict)


@dataclass
class RouteBrief:
    """A city pair, briefed: distance, block time, both ends' weather, CO₂.

    Returned by :meth:`skylink_api.resources.compose.Compose.route_brief`. Every
    part is independent, so they all go out in parallel and there is no primary:
    a failure anywhere lands in ``errors`` and the rest of the brief survives.

    Attributes:
        distance: Great-circle distance and initial bearing.
        flight_time: Model-predicted block time. ``estimated_hours_display`` is
            English prose (``"7h 23m"``) —
            :func:`skylink_api.helpers.units.parse_duration_minutes` turns it
            into a number.
        origin_metar: Departure field observation, decoded.
        destination_metar: Arrival field observation, decoded.
        origin_taf: Departure field forecast, decoded.
        destination_taf: Arrival field forecast, decoded.
        carbon: CO₂ estimate for the pair.
        errors: ``{part name: error}`` for the parts that failed.
    """

    distance: DistanceResponse | None = None
    flight_time: FlightTimePrediction | None = None
    origin_metar: MetarWithParsed | None = None
    destination_metar: MetarWithParsed | None = None
    origin_taf: TafWithParsed | None = None
    destination_taf: TafWithParsed | None = None
    carbon: CarbonEstimate | None = None
    errors: dict[str, SkyLinkError] = field(default_factory=dict)


@dataclass
class EnrichedAircraft:
    """One live ADS-B contact joined with its registry record.

    Returned in a list by
    :meth:`skylink_api.resources.compose.Compose.enrich_adsb`, one per input
    state and in input order.

    Attributes:
        state: The live feed row, exactly as it came in.
        info: The registry lookup for its ``icao24``. ``None`` when the aircraft
            broadcasts no address, when the per-call ``max_lookups`` budget was
            already spent (no request was made — check ``error`` to tell the two
            apart from a failure), or when the lookup failed. A **miss** is not
            ``None``: it is an :class:`~skylink_api.models.aircraft.AircraftLookup`
            with ``found=False``.
        error: The lookup failure, if there was one. ``None`` both when the
            lookup worked and when no lookup was attempted.
    """

    state: AdsbAircraft
    info: AircraftLookup | None = None
    error: SkyLinkError | None = None


@dataclass
class ScheduleWithStatus:
    """One row of a departure/arrival board joined with that flight's live status.

    Returned in a list by
    :meth:`skylink_api.resources.compose.Compose.schedules_with_status`, in board
    order.

    Attributes:
        entry: The board row. Its runtime class is
            :class:`~skylink_api.models.schedules.DepartureFlight` or
            :class:`~skylink_api.models.schedules.ArrivalFlight` depending on the
            direction asked for; both expose the shared columns of
            :class:`~skylink_api.models.schedules.ScheduleFlight`. Remember the
            row's own ``status`` is scraped board prose (``"Estimated 16:39"``),
            not the same thing as the flight status endpoint's.
        status: Live status of the row's flight number. ``None`` when the row
            carried no flight number (nothing was requested) or when the request
            failed.
        error: The status failure, if there was one. A flight number the status
            endpoint does not know is a 404 here, which is common for charter
            and cargo rows.
    """

    entry: ScheduleFlight
    status: FlightStatusResponse | None = None
    error: SkyLinkError | None = None
