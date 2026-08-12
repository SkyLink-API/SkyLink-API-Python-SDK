"""Resource namespaces.

One module per namespace, each exposing a ``Foo``/``AsyncFoo`` pair that the
client attaches as a ``cached_property``. You normally reach these through a
client (``sky.weather``, ``sky.adsb``, ...) rather than importing them, but the
classes are re-exported here for type annotations and for wrapping a namespace
in your own helper.

``FlightStatus`` and ``Distance`` are the exception: the client keeps its
instances private and surfaces them as the methods ``sky.flight_status(...)``
and ``sky.distance(...)``.
"""

from .adsb import Adsb, AsyncAdsb
from .aircraft import Aircraft, AsyncAircraft
from .airlines import Airlines, AsyncAirlines
from .airports import Airports, AirportType, AsyncAirports
from .briefing import AsyncBriefing, Briefing
from .carbon import AsyncCarbon, Carbon
from .charts import AsyncCharts, ChartCategory, Charts
from .delays import AsyncDelays, Delays
from .distance import AsyncDistance, Distance, DistanceUnit
from .flight_status import AsyncFlightStatus, FlightStatus
from .geo import AsyncGeo, Continent, Geo
from .history import AsyncHistory, History
from .ml import AsyncMl, Ml
from .navaids import AsyncNavaids, Navaids
from .notams import AsyncNotams, Notams
from .routes import AsyncRoutes, Routes
from .schedules import AsyncSchedules, Schedules
from .tickets import AsyncTickets, Tickets
from .weather import AsyncWeather, Weather
from .webhooks import AsyncWebhooks, Webhooks

__all__ = [
    "Adsb",
    "Aircraft",
    "Airlines",
    "AirportType",
    "Airports",
    "AsyncAdsb",
    "AsyncAircraft",
    "AsyncAirlines",
    "AsyncAirports",
    "AsyncBriefing",
    "AsyncCarbon",
    "AsyncCharts",
    "AsyncDelays",
    "AsyncDistance",
    "AsyncFlightStatus",
    "AsyncGeo",
    "AsyncHistory",
    "AsyncMl",
    "AsyncNavaids",
    "AsyncNotams",
    "AsyncRoutes",
    "AsyncSchedules",
    "AsyncTickets",
    "AsyncWeather",
    "AsyncWebhooks",
    "Briefing",
    "Carbon",
    "ChartCategory",
    "Charts",
    "Continent",
    "Delays",
    "Distance",
    "DistanceUnit",
    "FlightStatus",
    "Geo",
    "History",
    "Ml",
    "Navaids",
    "Notams",
    "Routes",
    "Schedules",
    "Tickets",
    "Weather",
    "Webhooks",
]
