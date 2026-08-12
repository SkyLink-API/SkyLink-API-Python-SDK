"""Response models for every SkyLink namespace.

Every model derives from :class:`SkyLinkModel` (``extra="allow"``), so unknown
fields from the API survive parsing and stay reachable through
``model_extra``. Import them from here or from the domain module directly::

    from skylink_api.models import Metar, NotamEntry
    from skylink_api.models.weather import Metar  # equivalent

No two domains export the same name, so this flat namespace needs no aliasing.

Two exceptions to "everything here is a pydantic model":
:class:`~skylink_api.models.poll.AdsbDiff` is a plain dataclass — it is computed
by the SDK from two consecutive responses, not parsed from one — and so is
everything in :mod:`skylink_api.models.compose`, which stitches several
responses together and carries the errors of the parts that failed.
"""

from ._base import SkyLinkModel
from .adsb import (
    AdsbAircraft,
    AdsbAircraftList,
    AdsbAltitudeStats,
    AdsbHealth,
    AdsbStatistics,
)
from .aircraft import (
    AircraftDatabaseStats,
    AircraftDetails,
    AircraftLookup,
    AircraftPerformance,
    AircraftPhoto,
)
from .airlines import Airline
from .airports import (
    Airport,
    AirportFrequency,
    AirportsByIPResponse,
    AirportsByLocationResponse,
    AirportSearchLocation,
    AirportsTextSearchResponse,
    AirportWithDistance,
    AirportWithRelevance,
    EnrichedAirport,
    IPLocation,
    Runway,
)
from .briefing import (
    AirportBriefing,
    BriefingNotam,
    BriefingPirep,
    BriefingRestriction,
    BriefingWeather,
    FlightBriefing,
    FlightBriefingText,
)
from .carbon import CarbonEstimate
from .charts import (
    Chart,
    ChartSource,
    ChartSourcesResponse,
    ChartsResponse,
)
from .common import Number
from .compose import (
    AirportBrief,
    EnrichedAircraft,
    FlightBrief,
    RouteBrief,
    ScheduleWithStatus,
)
from .delays import (
    AirspaceFlowProgram,
    Closure,
    FaaDelayResponse,
    GroundDelay,
    GroundStop,
)
from .distance import (
    Coordinates,
    DistanceResponse,
)
from .flight_status import (
    FlightStatusArrival,
    FlightStatusDeparture,
    FlightStatusLeg,
    FlightStatusResponse,
)
from .geo import (
    CountriesResponse,
    Country,
    CountryDetail,
    Region,
    RegionDetail,
    RegionsResponse,
)
from .history import (
    HistoryAirportTrafficResponse,
    HistoryFilters,
    HistoryFlight,
    HistoryFlightsResponse,
    HistoryPosition,
    HistoryPositionsResponse,
    HistoryTrackResponse,
)
from .ml import FlightTimePrediction
from .navaids import (
    Navaid,
    NavaidsResponse,
)
from .notams import (
    NotamEntry,
    NotamsResponse,
)
from .poll import AdsbDiff
from .routes import (
    AirlineRoute,
    AirlineRoutesResult,
    AirportRoutesResponse,
    CallsignRoute,
    RoutePair,
    RoutePairsResponse,
    VrsRouteResult,
)
from .schedules import (
    ArrivalFlight,
    ArrivalsResponse,
    DepartureFlight,
    DeparturesResponse,
    ScheduleFlight,
    ScheduleResponse,
)
from .tickets import (
    Layover,
    TicketLeg,
    TicketOffer,
    TicketSearchResponse,
)
from .weather import (
    AirSigmetCoord,
    AirSigmetMovement,
    AirSigmetObservation,
    AirSigmetReport,
    AirSigmetResponse,
    CloudLayer,
    Metar,
    MetarParsed,
    MetarWithParsed,
    PirepReport,
    PirepsResponse,
    Taf,
    TafParsed,
    TafPeriod,
    TafWithParsed,
    Visibility,
    WeatherReport,
    Wind,
    WindLevel,
    WindsAloftResponse,
    WindsAloftStation,
    WxCode,
)
from .webhooks import (
    Webhook,
    WebhookEvent,
    WebhookEventTypesResponse,
    WebhookListResponse,
    WebhookSubscription,
    WebhookToggleResponse,
)

__all__ = [
    "AdsbAircraft",
    "AdsbAircraftList",
    "AdsbAltitudeStats",
    "AdsbDiff",
    "AdsbHealth",
    "AdsbStatistics",
    "AirSigmetCoord",
    "AirSigmetMovement",
    "AirSigmetObservation",
    "AirSigmetReport",
    "AirSigmetResponse",
    "AircraftDatabaseStats",
    "AircraftDetails",
    "AircraftLookup",
    "AircraftPerformance",
    "AircraftPhoto",
    "Airline",
    "AirlineRoute",
    "AirlineRoutesResult",
    "Airport",
    "AirportBrief",
    "AirportBriefing",
    "AirportFrequency",
    "AirportRoutesResponse",
    "AirportSearchLocation",
    "AirportWithDistance",
    "AirportWithRelevance",
    "AirportsByIPResponse",
    "AirportsByLocationResponse",
    "AirportsTextSearchResponse",
    "AirspaceFlowProgram",
    "ArrivalFlight",
    "ArrivalsResponse",
    "BriefingNotam",
    "BriefingPirep",
    "BriefingRestriction",
    "BriefingWeather",
    "CallsignRoute",
    "CarbonEstimate",
    "Chart",
    "ChartSource",
    "ChartSourcesResponse",
    "ChartsResponse",
    "Closure",
    "CloudLayer",
    "Coordinates",
    "CountriesResponse",
    "Country",
    "CountryDetail",
    "DepartureFlight",
    "DeparturesResponse",
    "DistanceResponse",
    "EnrichedAircraft",
    "EnrichedAirport",
    "FaaDelayResponse",
    "FlightBrief",
    "FlightBriefing",
    "FlightBriefingText",
    "FlightStatusArrival",
    "FlightStatusDeparture",
    "FlightStatusLeg",
    "FlightStatusResponse",
    "FlightTimePrediction",
    "GroundDelay",
    "GroundStop",
    "HistoryAirportTrafficResponse",
    "HistoryFilters",
    "HistoryFlight",
    "HistoryFlightsResponse",
    "HistoryPosition",
    "HistoryPositionsResponse",
    "HistoryTrackResponse",
    "IPLocation",
    "Layover",
    "Metar",
    "MetarParsed",
    "MetarWithParsed",
    "Navaid",
    "NavaidsResponse",
    "NotamEntry",
    "NotamsResponse",
    "Number",
    "PirepReport",
    "PirepsResponse",
    "Region",
    "RegionDetail",
    "RegionsResponse",
    "RouteBrief",
    "RoutePair",
    "RoutePairsResponse",
    "Runway",
    "ScheduleFlight",
    "ScheduleResponse",
    "ScheduleWithStatus",
    "SkyLinkModel",
    "Taf",
    "TafParsed",
    "TafPeriod",
    "TafWithParsed",
    "TicketLeg",
    "TicketOffer",
    "TicketSearchResponse",
    "Visibility",
    "VrsRouteResult",
    "WeatherReport",
    "Webhook",
    "WebhookEvent",
    "WebhookEventTypesResponse",
    "WebhookListResponse",
    "WebhookSubscription",
    "WebhookToggleResponse",
    "Wind",
    "WindLevel",
    "WindsAloftResponse",
    "WindsAloftStation",
    "WxCode",
]
