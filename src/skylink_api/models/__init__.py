"""Response models for every SkyLink namespace.

Every model derives from :class:`SkyLinkModel` (``extra="allow"``), so unknown
fields from the API survive parsing and stay reachable through
``model_extra``. Import them from here or from the domain module directly::

    from skylink_api.models import Metar, NotamEntry
    from skylink_api.models.weather import Metar  # equivalent

No two domains export the same name, so this flat namespace needs no aliasing.
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
    WebhookEventTypesResponse,
    WebhookListResponse,
    WebhookSubscription,
    WebhookToggleResponse,
)

__all__ = [
    "AdsbAircraft",
    "AdsbAircraftList",
    "AdsbAltitudeStats",
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
    "EnrichedAirport",
    "FaaDelayResponse",
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
    "RoutePair",
    "RoutePairsResponse",
    "Runway",
    "ScheduleFlight",
    "ScheduleResponse",
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
