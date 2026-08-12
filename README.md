# SkyLink API — Python SDK

[![CI](https://github.com/skylinkapi/Python-SDK-/actions/workflows/ci.yml/badge.svg)](https://github.com/skylinkapi/Python-SDK-/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/skylink-api.svg)](https://pypi.org/project/skylink-api/)
[![Python](https://img.shields.io/pypi/pyversions/skylink-api.svg)](https://pypi.org/project/skylink-api/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Official Python client for the [SkyLink API](https://skylinkapi.com) — live ADS-B tracking,
aviation weather, airports and navaids, aerodrome charts, NOTAMs, FAA delays, flight status,
schedules, tickets, carbon estimates, AI pre-flight briefings and 90/365-day flight history.

Blocking and asyncio clients over the same surface, pydantic response models, retries with
jittered backoff, and a typed error hierarchy. Ships `py.typed`.

```python
from skylink_api import SkyLink

with SkyLink() as sky:                                  # RapidAPI, $RAPIDAPI_KEY
    metar = sky.weather.metar("KJFK", parsed=True)
    traffic = sky.adsb.aircraft(lat=51.47, lon=-0.46, radius=75)
    status = sky.flight_status("BA117")
```

## Install

```bash
pip install skylink-api
pip install "skylink-api[pandas]"     # optional: DataFrame conversion, see below
```

Requires Python 3.10+. Runtime dependencies: `httpx` and `pydantic` v2.

## Quickstart

### RapidAPI channel (default)

`SkyLink()` targets `https://skylink-api.p.rapidapi.com` (no version prefix — the listing
is pinned to v3.1), sends `X-RapidAPI-Key`/`X-RapidAPI-Host`, and reads the key from
`RAPIDAPI_KEY`.

```python
from skylink_api import SkyLink

with SkyLink() as sky:                                  # $RAPIDAPI_KEY
    metar = sky.weather.metar("KJFK")
    print(metar.raw)

# or pass the key explicitly
sky = SkyLink(api_key="...msh...jsn...")
```

### Direct channel

`provider="direct"` talks to `https://data.skylinkapi.com/v3.1` with an `x-api-key` header
and reads `SKYLINK_API_KEY`. Everything else is identical — same methods, same models.

```python
from skylink_api import SkyLink

with SkyLink(provider="direct") as sky:                 # $SKYLINK_API_KEY
    charts = sky.charts.by_airport("EGLL")
```

### API keys and environment variables

| Channel | Base URL | Auth header | Environment |
| --- | --- | --- | --- |
| `rapidapi` (default) | `https://skylink-api.p.rapidapi.com` | `X-RapidAPI-Key` + `X-RapidAPI-Host` | `RAPIDAPI_KEY`, then `SKYLINK_API_KEY` |
| `direct` | `https://data.skylinkapi.com/v3.1` | `x-api-key` | `SKYLINK_API_KEY` |

The default channel accepts `SKYLINK_API_KEY` as a fallback, so a key exported under the
neutral name is picked up by a plain `SkyLink()`. The reverse never happens: a RapidAPI
subscription key is not valid on `data.skylinkapi.com`, so `provider="direct"` never reads
`RAPIDAPI_KEY`. A blank variable counts as unset, and a missing key raises
`AuthenticationError` at construction time (unless you pass `base_url`).

### Async

`AsyncSkyLink` mirrors the sync surface method for method.

```python
import asyncio
from skylink_api import AsyncSkyLink

async def main() -> None:
    async with AsyncSkyLink() as sky:
        metar, traffic = await asyncio.gather(
            sky.weather.metar("EGLL"),
            sky.adsb.aircraft(lat=51.47, lon=-0.46, radius=75),
        )
        print(metar.raw, traffic.total_count)

asyncio.run(main())
```

One client owns one connection pool — build it once and share it, rather than per request.
Use `with` / `async with` (or `close()` / `await aclose()`) so the pool is released.

## Configuration

Every option is keyword-only and accepted by both clients.

| Option | Type | Default | Notes |
| --- | --- | --- | --- |
| `provider` | `"rapidapi" \| "direct"` | `"rapidapi"` | Selects the base URL, the auth header and the key env var. |
| `api_key` | `str \| None` | `$RAPIDAPI_KEY` → `$SKYLINK_API_KEY` (rapidapi), `$SKYLINK_API_KEY` (direct) | Missing key raises `AuthenticationError` at construction — unless `base_url` is set. |
| `base_url` | `str \| None` | provider default | Used verbatim; no version is appended. With it, a missing key is allowed (staging with `DISABLE_AUTH=true`). |
| `timeout` | `float \| httpx.Timeout \| None` | connect 5s, read/write 30s, pool 5s | `None` disables timeouts entirely. |
| `max_retries` | `int` | `3` | Applies to retryable statuses and transport failures. `0` disables. |
| `history_plan` | `"ultra" \| "mega"` | `"ultra"` | Picks the `/{plan}/history/...` prefix; overridable per call. |
| `default_headers` | `Mapping[str, str] \| None` | `{}` | Merged into every request (after auth, before per-call headers). |
| `http_client` | `httpx.Client \| httpx.AsyncClient \| None` | new client | Bring your own for proxies or a custom transport. |
| `sleep` | callable | `time.sleep` / `asyncio.sleep` | Backoff hook — injection point for tests. |
| `environ` | `Mapping[str, str] \| None` | `os.environ` | Environment used for the key lookup. |

Introspection on the client: `sky.api_key`, `sky.base_url`, `sky.provider`,
`sky.max_retries`, `sky.history_plan`, `sky.http_client`, the fully resolved `sky.config`,
and `sky.last_rate_limit`.

## Method index

Every method also accepts `request_options: RequestOptions | None` (see
[Retries and timeouts](#retries-and-timeouts)). Async methods are identical, awaited.
Namespaces are lazy `cached_property` objects: building a client costs nothing.

The SDK-side namespaces — `sky.batch`, `sky.poll`, `sky.compose` — plus the page iterators,
the pure helpers, the response cache and the pandas bridge are documented under
[Beyond the endpoints](#beyond-the-endpoints).

### `sky.weather`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `metar(icao, *, parsed=False)` | `GET /weather/metar/{icao}` | `Metar`, or `MetarWithParsed` when `parsed=True` |
| `taf(icao, *, parsed=False)` | `GET /weather/taf/{icao}` | `Taf`, or `TafWithParsed` when `parsed=True` |
| `winds_aloft(*, bbox, forecast=12, level="low")` | `GET /weather/winds-aloft` | `WindsAloftResponse` |
| `pireps(*, bbox, hours=2)` | `GET /weather/pireps` | `PirepsResponse` |
| `airsigmet(*, bbox, type=None)` | `GET /weather/airsigmet` | `AirSigmetResponse` |

`forecast` is `6 | 12 | 24`, `level` is `"low" | "high"`, `type` is `"airmet" | "sigmet"`.
Winds aloft is a US-only product.

### `sky.airports`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `search(*, icao=None, iata=None)` | `GET /airports/search` | `EnrichedAirport` |
| `nearby(*, lat, lon, radius=50, type=None, limit=50)` | `GET /airports/search/location` | `AirportsByLocationResponse` |
| `by_ip(*, ip=None, radius=100, type=None, limit=50)` | `GET /airports/search/ip` | `AirportsByIPResponse` |
| `search_text(*, q, limit=20, type=None)` | `GET /airports/search/text` | `AirportsTextSearchResponse` |

`search` needs exactly one of `icao`/`iata` (validated client-side). `type` filters on the
airport class: `"large_airport"`, `"medium_airport"`, `"small_airport"`, `"heliport"`,
`"seaplane_base"`, `"balloonport"`, `"closed"`.

### `sky.airlines`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `search(*, icao=None, iata=None)` | `GET /airlines/search` | `list[Airline]` |

At least one of `icao`/`iata` is required (validated client-side).

### `sky.navaids`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `list(*, ident=None, airport=None, type=None, country=None, bbox=None, limit=100)` | `GET /navaids` | `NavaidsResponse` |

At least one filter is required (validated client-side, before the request goes out).

### `sky.geo`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `countries(*, continent=None)` | `GET /countries` | `CountriesResponse` |
| `country(code)` | `GET /countries/{code}` | `CountryDetail` |
| `regions(*, country=None, continent=None)` | `GET /regions` | `RegionsResponse` |
| `region(code)` | `GET /regions/{code}` | `RegionDetail` |

`continent` is one of `"AF" | "AN" | "AS" | "EU" | "NA" | "OC" | "SA"`.

### `sky.adsb`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `aircraft(*, icao24=None, callsign=None, lat=None, lon=None, radius=None, bbox=None, min_alt=None, max_alt=None, min_speed=None, max_speed=None, registration=None, airline=None, photos=False, limit=None, offset=None)` | `GET /adsb/aircraft` | `AdsbAircraftList` |
| `statistics()` | `GET /adsb/aircraft/statistics` | `AdsbStatistics` |
| `health()` | `GET /adsb/health` | `AdsbHealth` |

`radius` is in **kilometres**. `lat`/`lon`/`radius` must be supplied together. This is the
only paginated endpoint (`limit`/`offset`); `total_count` is the match count *before* paging.

### `sky.aircraft`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `by_registration(registration, *, photos=True)` | `GET /aircraft/registration/{registration}` | `AircraftLookup` |
| `by_icao24(icao24, *, photos=True)` | `GET /aircraft/icao24/{icao24}` | `AircraftLookup` |
| `performance(icao_type)` | `GET /aircraft/performance/{icao_type}` | `AircraftPerformance` |
| `database_stats()` | `GET /aircraft/database/stats` | `AircraftDatabaseStats` |

An unknown airframe is a `200` with `found=False` and `aircraft=None`, not a 404.

### `sky.charts`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `by_airport(icao, *, source=None)` | `GET /charts/{icao}` | `ChartsResponse` |
| `by_category(icao, category, *, source=None)` | `GET /charts/{icao}/{category}` | `ChartsResponse` |
| `sources()` | `GET /charts/sources` | `ChartSourcesResponse` |

`category` is `"GEN" | "GND" | "SID" | "STAR" | "APP"`. `ChartsResponse.charts` is a
`dict[str, list[Chart]]` keyed by category — a category with no charts is simply absent.

### `sky.delays`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `faa(icao=None)` | `GET /delays/faa` or `GET /delays/faa/{icao}` | `FaaDelayResponse` |

US airports only. Durations (`avg_delay`, `max_delay`) and times are opaque strings.

### `sky.notams`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `by_airport(icao, *, exclude_qcode=None, exclude_scope=None, include_future=False)` | `GET /notams/{icao}` | `NotamsResponse` |

`exclude_qcode`/`exclude_scope` take a CSV string or a sequence; scopes are `"AERODROME"`
and `"FIR"`.

### `sky.schedules`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `departures(*, icao=None, iata=None, date=None, time=None, ts=None)` | `GET /schedules/departures` | `DeparturesResponse` |
| `arrivals(*, icao=None, iata=None, date=None, time=None, ts=None)` | `GET /schedules/arrivals` | `ArrivalsResponse` |

Exactly one of `icao`/`iata` is required. `date` accepts `date`/`datetime`/`str` and is sent
as `DD-MM-YYYY`.

### `sky.ml`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `flight_time(*, origin, destination, aircraft=None)` | `GET /ml/flight-time` | `FlightTimePrediction` |

`origin`/`destination` are serialised to the wire keys `from`/`to`.

### `sky.carbon`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `estimate(*, departure_icao=None, arrival_icao=None, callsign=None, aircraft_type=None, passengers=None, include_rfi=False)` | `GET /carbon/estimate` | `CarbonEstimate` |

Either the airport pair **or** a `callsign` (which the API resolves to a route) is required.

### `sky.briefing`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `flight(*, origin, destination, include_weather=True, include_notams=True, include_pireps=False, format="json")` | `GET /briefing/flight` | `FlightBriefing` for `format="json"`, otherwise `str` |
| `pdf(*, departure_icao, arrival_icao, flight_number=None)` | `GET /briefing/pdf` | `bytes` |

`format` is `"json" | "markdown" | "plain_text" | "html"`; the overload makes the static type
follow the argument. Text formats arrive inside a JSON envelope which the SDK unwraps.

### `sky.routes`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `by_callsign(callsign)` | `GET /routes/callsign/{callsign}` | `VrsRouteResult \| AirlineRoutesResult` |
| `by_airport(code, *, direction="both", limit=100)` | `GET /routes/airport/{code}` | `AirportRoutesResponse` |
| `pairs(*, departure=None, arrival=None, limit=50)` | `GET /routes/pairs` | `RoutePairsResponse` |

`by_callsign` returns a union discriminated by `source`: `"vrs"` (an exact route) or
`"airline_routes"` (the operator's network when the exact flight is unknown). `direction` is
`"dep" | "arr" | "both"`.

### `sky.tickets`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `search(*, origin, destination, date=None, passengers=1)` | `GET /tickets/search` | `TicketSearchResponse` |

`date` accepts `date`/`datetime`/`str` and is sent as `YYYY-MM-DD`.

### `sky.webhooks`

| Method | Endpoint | Returns |
| --- | --- | --- |
| `create(*, url, event_types, filters=None)` | `POST /webhooks` → `201` | `Webhook` |
| `list()` | `GET /webhooks` | `list[WebhookSubscription]` |
| `update(webhook_id, *, active)` | `PATCH /webhooks/{id}` | `WebhookToggleResponse` |
| `delete(webhook_id)` | `DELETE /webhooks/{id}` → `204` | `None` |
| `event_types()` | `GET /webhooks/events` | `list[str]` |

`event_types` values: `"status_changed"`, `"flight_delayed"`, `"flight_cancelled"`,
`"flight_boarding"`, `"flight_landed"`, `"gate_changed"`.

### `sky.history`

Every method takes `plan: "ultra" | "mega" | None` — per-call plan beats the client's
`history_plan`, which beats `"ultra"`.

| Method | Endpoint | Returns |
| --- | --- | --- |
| `flights(*, start=None, end=None, icao24=None, registration=None, callsign=None, departure_icao=None, arrival_icao=None, limit=None, plan=None)` | `GET /{plan}/history/flights` | `HistoryFlightsResponse` |
| `flight(flight_id, *, plan=None)` | `GET /{plan}/history/flight/{flight_id}` | `HistoryFlight` |
| `track(flight_id, *, limit=None, plan=None)` | `GET /{plan}/history/flight/{flight_id}/track` | `HistoryTrackResponse` |
| `positions(ident, *, start=None, end=None, limit=None, plan=None)` | dispatches on `ident` | `HistoryPositionsResponse` |
| `positions_by_icao24(icao24, *, start=None, end=None, limit=None, plan=None)` | `GET /{plan}/history/positions/{icao24}` | `HistoryPositionsResponse` |
| `positions_by_registration(registration, *, start=None, end=None, limit=None, plan=None)` | `GET /{plan}/history/positions/registration/{registration}` | `HistoryPositionsResponse` |
| `airport_traffic(icao, *, direction="both", start=None, end=None, limit=None, plan=None)` | `GET /{plan}/history/airport/{icao}/traffic` | `HistoryAirportTrafficResponse` |

`start`/`end` accept `date`/`datetime`/`str` and are sent as ISO 8601. `positions()` treats a
6-hex-character `ident` as an ICAO24 address and anything else as a registration.

### Client methods

| Method | Endpoint | Returns |
| --- | --- | --- |
| `sky.flight_status(flight_number)` | `GET /flight_status/{flight_number}` | `FlightStatusResponse` |
| `sky.distance(*, from_icao=None, to_icao=None, from_lat=None, from_lon=None, to_lat=None, to_lon=None, unit="nm")` | `GET /distance` | `DistanceResponse` |
| `sky.request(method, path, *, query=None, json_body=None, headers=None, response_kind="json", cast_to=None, options=None)` | any | decoded payload |

These two endpoints have a single operation each, so they live directly on the client rather
than in a namespace. Each end of `distance` is given either as an airport code or as a
lat/lon pair, and the two styles mix freely; `unit` is `"nm" | "km" | "mi"`.

`request()` is the escape hatch for anything this SDK does not model yet — same auth, retries
and error handling, with an optional pydantic `cast_to`:

```python
raw = sky.request("GET", "/weather/metar/KJFK", query={"parsed": True})
```

## Beyond the endpoints

Everything below is SDK-side: three extra namespaces that combine calls, iterators over the
paged endpoints, a module of pure helpers, an opt-in cache and the pandas bridge. No new
runtime dependency, and nothing here changes how a plain endpoint call behaves.

### `sky.batch` — one call, many identifiers

The API is one-identifier-per-request. `sky.batch` fans that out with bounded concurrency
(default 5, because of marketplace quotas), collapses duplicates and returns a
`{identifier: value | SkyLinkError}` mapping — **one bad code costs one value, not the batch**.

```python
from skylink_api import SkyLinkError
from skylink_api.helpers.batch import failures, successes

reports = sky.batch.metars(["EGLL", "KJFK", "ZZZZ"], concurrency=3)
for icao, report in reports.items():        # keys are your strings, in input order
    if isinstance(report, SkyLinkError):
        print(icao, "unavailable")
    else:
        print(icao, report.raw)

good, bad = successes(reports), failures(reports)
```

| Method | Per identifier | Value type |
| --- | --- | --- |
| `batch.metars(icaos)` | `GET /weather/metar/{icao}` | `Metar` |
| `batch.tafs(icaos)` | `GET /weather/taf/{icao}` | `Taf` |
| `batch.notams(icaos)` | `GET /notams/{icao}` | `NotamsResponse` |
| `batch.airports(codes)` | `GET /airports/search` | `EnrichedAirport` |
| `batch.flight_statuses(numbers)` | `GET /flight_status/{number}` | `FlightStatusResponse` |

All take `concurrency=5` and `request_options=None`. `batch.airports()` picks `icao=` or
`iata=` from the shape of each code; OurAirports pseudo-codes (`GB-0888`) cannot be resolved
by that endpoint and land in the result as errors — filter them with
`helpers.idents.is_local_pseudocode` first. `helpers.batch.raise_for_errors(results)` turns
the first failure into an exception when a partial answer is not acceptable, and
`helpers.batch.map_concurrent` / `amap_concurrent` are the same primitive for your own calls.

### `sky.compose` — the page, not the endpoint

An "airport page" is eight requests; a "flight page" is four. `sky.compose` issues them in
parallel and returns one dataclass. **A part that fails is `None` and its error lands in
`.errors[part]` — the aggregate degrades, it does not raise.** The single exception is the
primary request (`airports.search` for `airport_brief`, the flight status for `flight_brief`),
without which the result would be meaningless.

```python
brief = sky.compose.airport_brief("EGLL", schedules_limit=5)
print(brief.metar.raw if brief.metar else "no observation")
print(brief.errors)          # {'delays': NotFoundError(...)} — EGLL is not an FAA field
```

| Method | Returns | Notes |
| --- | --- | --- |
| `compose.airport_brief(icao, *, include=None, exclude=None, schedules_limit=10)` | `AirportBrief` | airport, metar, taf, notams, delays, charts, departures, arrivals |
| `compose.flight_brief(number, *, include=None, exclude=None)` | `FlightBrief` | status → airframe → route → CO2 (a chain, not a fan-out) |
| `compose.route_brief(origin, destination, *, include=None, exclude=None, aircraft_type=None, passengers=None)` | `RouteBrief` | distance, block time, both ends' weather, CO2 |
| `compose.enrich_adsb(states, *, concurrency=5, max_lookups=50, photos=False)` | `list[EnrichedAircraft]` | joins live contacts with the airframe registry, memoised per `icao24` |
| `compose.schedules_with_status(icao, *, direction="departures", limit=10, concurrency=5)` | `list[ScheduleWithStatus]` | board rows plus each flight's live status |
| `compose.north_america_countries()` | `list[Country]` | **backend-bug workaround**, see below |

`include=` is the exact set of parts to request (so an unwanted part costs no quota),
`exclude=` subtracts from the full set; passing both is a `ValueError`, and the part names are
the result's own field names (`AIRPORT_BRIEF_PARTS`, `FLIGHT_BRIEF_PARTS`, `ROUTE_BRIEF_PARTS`
in `skylink_api.resources.compose`). A part that was never requested is `None` with **no**
entry in `errors`, so "not asked for" and "asked for and failed" stay distinguishable.

> `north_america_countries()` exists because `geo.countries(continent="NA")` returns nothing:
> the backend reads its CSV with pandas, which parses the literal `NA` as *not-a-number*, so
> every North American country arrives with `continent: null`. The method fetches the full
> list and returns the rows whose continent is empty (or already `"NA"`, so it keeps working
> once the backend is fixed). Same warning is attached to the `CONTINENTS` constant.

### Iterators and pollers

Paging and "ask again in a minute" are the two loops every integration writes by hand.

```python
for aircraft in sky.adsb.iter_aircraft(bbox=box, page_size=100, max_items=500):
    ...                       # the only paginated endpoint; stops on a short page

for flight in sky.history.iter_flights(registration="G-STBA", window_days=7, max_items=50):
    ...                       # slices a long range into per-window requests, newest first

for diff in sky.poll.adsb(bbox=box, interval=10, max_iterations=6):
    if diff.is_first:
        draw_all(diff.snapshot.values())
        continue
    add(diff.appeared); remove(diff.disappeared); move(diff.updated)

for status in sky.poll.flight_status("BA117", interval=60):
    print(status.status)      # only when it changed; stops itself once the flight lands
```

* The first request goes out immediately; `interval` is the pause **between** requests.
* `429` and `5xx` are survived (waiting out `Retry-After`) and count against `max_iterations`;
  `401`/`403`/`422` propagate — a wrong key never fixes itself.
* `poll.adsb` yields an `AdsbDiff` (`appeared`, `disappeared` as `icao24` strings, `updated`,
  `snapshot`, `is_first`); "updated" means position, altitude or ground speed moved —
  `last_seen` is deliberately ignored, or every aircraft would be updated on every tick.
* `poll.flight_status` compares status prose plus times, gates, terminals and the baggage belt,
  with `""`/`"--"` folded to "unknown"; terminal is a case-insensitive substring match on
  landed/arrived/cancelled/diverted. Pair `until_terminal=True` with `max_iterations` for a
  flight number you do not trust — an unknown flight stays `"Unknown"` forever.
* `sleep=` is injectable on both, and the async client returns `AsyncIterator`s from the same
  method names.

### `skylink_api.helpers` — pure functions, no client

```python
from skylink_api import helpers
from skylink_api.helpers.geojson import adsb_to_geojson
from skylink_api.helpers.weather import flight_category

box = helpers.bbox_around(51.4706, -0.4619, radius_km=60)
live = sky.adsb.aircraft(bbox=box)
layer = adsb_to_geojson(live)                         # [lon, lat], per RFC 7946
category = flight_category(sky.weather.metar("EGLL", parsed=True))    # 'VFR' | 'MVFR' | ...
```

| Module | Contents |
| --- | --- |
| `helpers.units` | `ft_to_m`, `kt_to_kmh`, `inhg_to_hpa`, `c_to_f`, … plus `normalize_altimeter` (the API sends pressure **without a unit**), `parse_visibility` (`"P6SM"`, `"M1/4SM"`, `9999`), `parse_duration_minutes`/`parse_duration` (`"7h 23m"`), `humidity_to_percent` |
| `helpers.spatial` | `bbox`, `bbox_around`, `parse_bbox`, `haversine_km`/`_nm`, `initial_bearing`, `destination_point`, `great_circle_points`, `track_stats`, `simplify_track`, `point_coords` |
| `helpers.weather` | `flight_category`, `ceiling_ft`, `metar_age`, `is_stale`, `wind_components` (head/tail and crosswind for a runway) |
| `helpers.geojson` | `adsb_to_geojson`, `track_to_geojson`, `airports_to_geojson`, `navaids_to_geojson` — plain `TypedDict`s, always `[longitude, latitude]` |
| `helpers.idents` | `classify_airport_code`, `is_local_pseudocode`, `is_icao24`, `normalize_icao24`, `normalize_registration`, `split_flight_number` |
| `helpers.sentinels` | `is_found`/`require_found`, `has_results`/`require_results`, `require_ip_result` — turn the 200-with-a-sentinel answers into exceptions where a miss *is* fatal |
| `helpers.batch` | `map_concurrent`, `amap_concurrent`, `successes`, `failures`, `raise_for_errors` |
| `helpers.cache` | `MemoryCache`, `CacheProtocol` — see below |

Every converter accepts `str | float | int | None` (the API serves numbers as strings often
enough) and returns `None` rather than raising on input it cannot read.

### Response cache and quota hooks

The cache is **off by default**, and a bare `MemoryCache()` is inert: TTLs are opt-in per
operation. Only successful `GET`s are cached, keyed by
`provider | base_url | METHOD path?sorted-query`, and a hit re-validates the stored payload,
so a caller who mutates a returned model cannot corrupt the next one's copy.

```python
from skylink_api import MemoryCache, SkyLink

cache = MemoryCache(ttls={"weather.metar": 60, "airports.*": 3600, "geo.*": 86_400})
with SkyLink(cache=cache) as sky:                 # "adsb.aircraft" left out on purpose
    sky.weather.metar("EGLL")                     # network
    sky.weather.metar("EGLL")                     # cache

    stop = sky.on_rate_limit(lambda info: print(info.remaining, "of", info.limit))
    sky.on_quota_low(warn, threshold=0.1)         # edge-triggered: fires once per window
    stop()                                        # unsubscribe
```

TTL lookup is by exact operation name, then by namespace prefix (`"weather.*"`), then
`default_ttl`; `0` means "do not cache". Any store with `get(key)`/`set(key, value, ttl)`
satisfies `CacheProtocol` (Redis, diskcache, …), and a cache that raises is degraded to no
cache with a `RuntimeWarning` rather than failing the request. `on_rate_limit` receives the
snapshot of *that* response (unlike `last_rate_limit`, which is last-writer-wins under
concurrency); a hook that raises is reported as a warning and never breaks the call.

### `from_env` and `with_options`

```python
sky = SkyLink.from_env(provider="direct")         # key can only come from the environment
patient = sky.with_options(timeout=120.0, max_retries=0)
archive = sky.with_options(history_plan="mega")
```

`with_options` reuses the same `httpx` client — no second connection pool — copies the
registered hooks as a snapshot and shares the cache unless you pass `cache=`. Ownership of the
transport stays with the original: keep it alive for as long as any clone is in use.

### pandas

```python
from skylink_api.pandas_ext import to_dataframe          # pip install "skylink-api[pandas]"

frame = to_dataframe(sky.adsb.aircraft(bbox=box))        # rows from the "aircraft" field
frame = to_dataframe(sky.schedules.departures("EGLL"))   # ... "flights"
frame = to_dataframe(sky.history.track(flight_id))       # ... "positions"
frame = to_dataframe(page.aircraft)                      # a bare list works too
```

`to_dataframe` is a free function, not a model method: pandas stays out of the SDK's own type
annotations and is imported on first call. It unwraps the list-shaped envelopes
(`aircraft`, `positions`, `flights`, `navaids`, `countries`, `regions`, `airports`, `notams`,
`reports`, `stations`, `routes`, `sources` — `pandas_ext.LIST_FIELDS`), a bare list of models
or a bare list of dicts; `field="flights"` picks the other list on a response that carries
two. Nothing is coerced on the way in, so the string-typed columns described under
[Gotchas](#gotchas) stay strings. Without pandas installed the call raises `ImportError`
naming the extra.

## Error handling

```
SkyLinkError
├── APIConnectionError            transport failed, no HTTP response
│   └── APITimeoutError           connect/read/write timeout
├── APIResponseValidationError    2xx body did not match the model (.body keeps the payload)
└── APIStatusError                non-2xx (.status_code, .headers, .body, .code, .errors)
    ├── BadRequestError           400
    ├── AuthenticationError       401 — also raised at construction with no key
    ├── PermissionDeniedError     403 — plan does not cover this call
    ├── NotFoundError             404
    ├── UnprocessableEntityError  422 — .errors holds the per-field items
    ├── RateLimitError            429 — .retry_after, .rate_limit
    └── InternalServerError       500 and other 5xx
        └── ServiceUnavailableError  503 — an upstream source is not ready
```

```python
from skylink_api import APIStatusError, NotFoundError, RateLimitError, SkyLink

with SkyLink() as sky:
    try:
        metar = sky.weather.metar("ZZZZ")
    except NotFoundError:
        metar = None
    except RateLimitError as err:
        print(err.retry_after, err.rate_limit)
        raise
    except APIStatusError as err:
        print(err.status_code, err.message, err.body)
        raise
```

### Three body shapes, one message

The API answers errors in three formats and the SDK normalises all of them into `.message`:

| Shape | Example | Exposed as |
| --- | --- | --- |
| Gateway 401 | `{"error": "Unauthorized", "message": "...", "code": "MARKETPLACE_ACCESS_REQUIRED"}` | `.message`, `.code` |
| `HTTPException` | `{"detail": "Airport not found"}` | `.message` |
| Validation 422 | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` | `.message` (joined) + `.errors` |

Anything else (HTML from a proxy, an empty body) degrades to `HTTP <status>` with the raw
payload kept on `.body`.

### "Not found" that is not an error

Three endpoints report absence with a `200` and a sentinel field. These are typed values, not
exceptions:

```python
lookup = sky.aircraft.by_registration("N0000X")
if not lookup.found:                       # aircraft is None
    ...

result = sky.airports.by_ip()
if result.error:                           # IP geolocation failed
    ...

flights = sky.history.flights(registration="G-STBA")
if flights.count == 0:
    print(flights.note)                    # "Registration 'G-ZZZZ' not found ..."
```

### Quota

`sky.last_rate_limit` holds a `RateLimitInfo(limit, remaining, reset)` parsed from the quota
headers of the most recent response. Both channels are understood: RapidAPI sends
`X-RateLimit-Requests-*` (the plan's request quota, which wins over the marketplace's noisier
`X-RateLimit-rapid-free-plans-hard-limit-*` counters), the direct gateway sends
`X-RateLimit-*`. It stays `None` when the response carried no quota headers at all — for
example against a staging instance behind neither gateway. On a 429 the same snapshot is on
`RateLimitError.rate_limit`.

## Retries and timeouts

* Retried: `429`, `500`, `502`, `503`, `504`, plus connection and timeout failures.
* Never retried: `400`, `401`, `403`, `404`, `422`.
* `POST` is replayed on `429` only — a throttled request provably never reached the handler,
  so `POST /webhooks` cannot be duplicated by a retry. After a 5xx or a transport error it is
  not replayed.
* Backoff: full jitter, `random() * min(8s, 0.5s * 2 ** attempt)`. A `Retry-After` header
  (delta-seconds or HTTP-date) wins and is capped at 60s.
* Default `max_retries` is 3, so a failing call is attempted at most 4 times.
* Default timeout: connect 5s, read 30s, write 30s, pool 5s.

Both are overridable per call through `request_options`, alongside extra headers and query
parameters:

```python
metar = sky.weather.metar(
    "KJFK",
    request_options={"timeout": 5.0, "max_retries": 0, "headers": {"X-Trace-Id": "abc"}},
)
```

## Gotchas

* **Schedules use PascalCase on the wire.** `ScheduleFlight` keys arrive as `Time`, `Date`,
  `IATA`, `Flight`, `Airline`, `Status`, `Destination`/`Origin`; the model exposes them as
  snake_case attributes through aliases. `flight.destination`, not `flight["Destination"]`.
* **Opaque time strings stay strings.** `flight_status` times, NOTAM `effective`/`expiration`
  and the FAA delay durations are scraped in whatever format the source used (`"14:25"`,
  `"--"`, `""`, `"1 hour 30 minutes"`). The SDK never guesses a `datetime` for them. Genuine
  ISO 8601 fields (ADS-B `last_seen`, history timestamps, webhook `created_at`) *are* parsed
  into `datetime`.
* **`ml.flight_time` renames its arguments.** You pass `origin=`/`destination=`; the wire keys
  are `from`/`to` (reserved words in Python, valid on the query string).
* **`ultra` vs `mega` is a path, not a parameter.** `/ultra/history/...` covers 90 days
  (1 000 flights / 5 000 positions per call), `/mega/history/...` covers 365 days (2 000 /
  10 000). Calling a plan the key is not subscribed to is a `403`.
* **`briefing.pdf()` returns `bytes`.** It is the only non-JSON endpoint — write it out with
  `pathlib.Path("brief.pdf").write_bytes(pdf)`.
* **`Airline.active` is a string.** `"Y"` or `"N"`, not a bool. Same policy elsewhere: values
  the upstream serves as strings stay strings — `Navaid.frequency_khz`,
  `AirportFrequency.frequency_mhz`, `AircraftDetails.year_built`.
* **`bbox` is `(lat1, lon1, lat2, lon2)`, south-west corner first**, serialised to one comma
  separated string. Boxes with `lat1 >= lat2` or `lon1 >= lon2` are rejected with a 400.
* **`photos` defaults differ.** `False` on `adsb.aircraft()` (slow, only covers the first 50
  rows of a page), `True` on the `aircraft.by_*()` lookups.
* **Unknown fields survive.** Every model allows extras, so a field the upstream adds
  tomorrow is preserved on the instance instead of raising. Responses are validated
  best-effort; a shape change that breaks a *declared* field raises
  `APIResponseValidationError` with the raw payload on `.body`.
* **Some calls are validated before sending** — `navaids.list()`, `airlines.search()`,
  `airports.search()`, `carbon.estimate()` and `schedules.*` raise `ValueError` on a missing
  or ambiguous selector instead of spending a round trip on a guaranteed 400.

## Examples

Runnable scripts in [`examples/`](examples):

| File | Shows |
| --- | --- |
| [`weather.py`](examples/weather.py) | METAR raw and decoded, TAF, winds aloft by bounding box |
| [`adsb_tracking.py`](examples/adsb_tracking.py) | Live traffic in a radius, paging, `last_rate_limit`, feed statistics and health |
| [`flight_briefing.py`](examples/flight_briefing.py) | Structured vs markdown briefing (the `format` overload), saving the PDF |
| [`history.py`](examples/history.py) | Archived flights, track, both position lookups, the `mega` plan |
| [`webhooks.py`](examples/webhooks.py) | Full create → list → update → delete cycle plus `event_types()` |
| [`async_usage.py`](examples/async_usage.py) | `AsyncSkyLink` with `asyncio.gather` fan-out |
| [`batch_requests.py`](examples/batch_requests.py) | `sky.batch` over many identifiers, reading successes and failures |
| [`airport_brief.py`](examples/airport_brief.py) | `sky.compose.airport_brief` / `route_brief`, `include=`, and printing `errors` |
| [`polling.py`](examples/polling.py) | `poll.adsb` diffs, `poll.flight_status` until landed, `iter_aircraft` |
| [`map_export.py`](examples/map_export.py) | `bbox_around`, `flight_category`, `wind_components`, GeoJSON layers written to disk |
| [`cache_and_quota.py`](examples/cache_and_quota.py) | `MemoryCache` TTLs, `on_rate_limit`/`on_quota_low`, `from_env`, `with_options` |

```bash
export RAPIDAPI_KEY=...msh...jsn...     # or SKYLINK_API_KEY with provider="direct"
python examples/weather.py
```

Full API documentation: <https://skylinkapi.com/docs>.

## Contributing

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

pytest                           # unit suite, no network
mypy src/skylink_api             # strict
ruff check src tests
ruff format src tests
```

Tests never touch the network: HTTP is mocked with `respx` and backoff sleeps are injected.
Fixtures under `tests/fixtures/` are extracted from the backend routers — see
`tests/fixtures/SOURCES.md` for the file:line provenance of each one.

Integration tests are gated on an environment variable and skipped otherwise:

```bash
SKYLINK_TEST_API_KEY=...msh...jsn... pytest tests/integration          # RapidAPI (default)
SKYLINK_TEST_BASE_URL=http://localhost:8081/v3.1 pytest tests/integration
SKYLINK_TEST_PROVIDER=direct SKYLINK_TEST_API_KEY=sk_live_... pytest tests/integration
```

Publishing is automated: pushing a `v*` tag builds the distribution and uploads it to PyPI via
trusted publishing (the publisher must be configured on PyPI for this repository first).

## License

MIT — see [LICENSE](LICENSE).
