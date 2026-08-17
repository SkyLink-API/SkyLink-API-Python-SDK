# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **`briefing.flight()` and `briefing.pdf()` timed out on every call with the default
  client.** A briefing is composed by a language model over both airports' weather and
  NOTAMs and takes far longer than the 30 s `DEFAULT_TIMEOUT`: measured live on
  2026-08-15, `format="json"` took 30–128 s, `markdown` 30–50 s, `plain_text` up to 85 s
  and the PDF ~52 s. Every one of those aborted, and because a timeout is retried the
  caller waited ~120 s to be told a healthy endpoint had failed. Both routes now carry a
  new `BRIEFING_TIMEOUT` (180 s read) on the request spec itself.

  This is the first use of the new `RequestSpec.timeout` field, an endpoint-level
  *default*: an explicit `request_options={"timeout": ...}` still wins, and every other
  endpoint keeps the client's timeout untouched. Retries are deliberately left on — a real
  503 should still be retried — so cap both yourself where a slow page is worse than no
  briefing:

  ```python
  sky.briefing.flight(
      origin="KJFK",
      destination="KLAX",
      request_options={"timeout": 60.0, "max_retries": 0},
  )
  ```

### Changed

Documentation catching up with backend fixes shipped in the 2026-08 API release. No
behaviour changes — every one of these endpoints already worked through the SDK once the
server side was corrected; what was wrong was the SDK telling users they were broken.

- `CONTINENTS` / `geo.countries(continent="NA")` no longer carry the "accepted but resolves
  to nothing" warning. The backend used to read its reference CSV with pandas, which parses
  the literal `NA` as *not-a-number*, so North America was unqueryable. Verified live on
  2026-08-15: 41 countries and 440 regions.
- `compose.north_america_countries()` is documented as a convenience rather than a
  workaround, and its **TODO: delete** note is gone. The method is kept — it is public API,
  and its predicate still accepts the historical `null`/`""` spellings alongside `"NA"`, so
  it answers correctly against an older deployment. `geo.countries(continent="NA")` is now
  the cheaper route and is documented as such.
- `TicketOffer.original_price` / `.original_currency` are no longer documented as absent.
  The ticket service emits them (`JFK→LAX`: `price_usd=168.52`, `original_price=137.0`,
  `original_currency="CHF"`), which finally makes the `price_usd`-is-not-always-USD caveat
  actionable.
- `TicketSearchResponse.count` no longer claims a 15-offer cap: `JFK→LAX` returned 111
  offers and `LHR→JFK` 120.
- The `weather.airsigmet` live test lost its `xfail` marker — the endpoint answered a bare
  `500` for every bbox and now serves normally, so a 500 is a failure again. A second test
  covers the `type=` filter and the `filter_type` echo, which the 500 hid entirely.

### Added

- `batch.metars()` and `batch.tafs()` take `parsed=` (both `Batch` and `AsyncBatch`),
  overloaded so `parsed=True` narrows the result to
  `dict[str, MetarWithParsed | SkyLinkError]` / `dict[str, TafWithParsed | SkyLinkError]`.
  Without it the batch could only return undecoded reports, and every function in
  `helpers.weather` — `flight_category()`, `ceiling_ft()`, the unit parsers — silently
  answered `None` on them, because they read decoded fields and never re-parse the raw
  text. A weather board coloured by flight category is the main reason to batch METARs,
  so the omission made the namespace unusable for its headline case. `compose.airport_brief`
  and `compose.route_brief` already requested `parsed=True` for exactly this reason; `batch`
  was the outlier. The default stays `False`, so no existing call changes behaviour.

## [0.1.0] - Unreleased

First public release. Covers the SkyLink API v3.1 surface.

### Added

**Clients**

- `SkyLink` (blocking) and `AsyncSkyLink` (asyncio) over `httpx`, method for method
  identical. Both are context managers (`with` / `async with`) plus explicit
  `close()` / `aclose()`, and each owns a single connection pool.
- Two delivery channels through one `provider` option: `rapidapi` — **the default** —
  (`https://skylink-api.p.rapidapi.com`, no version prefix, `X-RapidAPI-Key`/`X-RapidAPI-Host`,
  `$RAPIDAPI_KEY` and `$SKYLINK_API_KEY` as a fallback) and `direct`
  (`https://data.skylinkapi.com/v3.1`, `x-api-key`, `$SKYLINK_API_KEY` only — a marketplace
  key is never reused on the direct host). A custom `base_url` is used verbatim and makes the
  key optional, so staging instances running `DISABLE_AUTH=true` work out of the box.
- Configuration: `provider`, `api_key`, `base_url`, `timeout`, `max_retries`,
  `history_plan`, `default_headers`, `http_client`, `sleep`, `environ`.
- `client.request()` escape hatch — same auth, retries and error handling as the typed
  methods, with an optional pydantic `cast_to`.
- `client.last_rate_limit`: `RateLimitInfo(limit, remaining, reset)` parsed from the quota
  headers of the last response, on **either** channel — `X-RateLimit-Requests-*` (RapidAPI's
  plan quota, preferred) or `X-RateLimit-*` (the direct gateway). The marketplace's
  `X-RateLimit-rapid-free-plans-hard-limit-*` counters describe the free tier rather than the
  caller's plan and are deliberately ignored.

**Namespaces** (18 namespaces, 49 methods, plus 2 client-level shortcuts)

- `weather` — METAR, TAF (both with an `@overload` on `parsed`), winds aloft, PIREPs,
  AIRMET/SIGMET.
- `airports` — code search, radius search, IP geolocation search, free-text search.
- `airlines` — code search. `navaids` — filtered search. `geo` — countries and regions,
  list and detail.
- `adsb` — live aircraft (12 filters, and the only paginated endpoint), feed statistics,
  ingest health.
- `aircraft` — registration and ICAO24 lookup, type performance, database stats.
- `charts` — by airport, by category, sources. `notams` — by airport.
  `delays` — FAA NAS status, nationwide or per airport.
- `schedules` — departure and arrival boards. `ml` — flight-time prediction.
  `carbon` — CO2 estimate. `tickets` — fare search.
- `briefing` — flight briefing as JSON or as a rendered document (`@overload` on
  `format`), plus the PDF endpoint returning `bytes`.
- `routes` — callsign resolution (a union discriminated by `source`), airport routes,
  route pairs.
- `webhooks` — full CRUD: `POST` → 201, `GET`, `PATCH`, `DELETE` → 204, plus the event
  type catalogue.
- `history` — archived flights, single flight, track, positions by ICAO24 or
  registration (with a dispatching `positions()`), airport traffic; `ultra`/`mega` plan
  selected per call or per client.
- `client.flight_status()` and `client.distance()` — the two single-operation endpoints,
  exposed directly on the client.

**Types and models**

- Over 100 pydantic v2 response models, all `extra="allow"` and `populate_by_name=True`, so a
  new upstream field is preserved instead of raising. `py.typed` ships with the package
  (PEP 561) and the source is `mypy --strict` clean.
- Wire quirks handled in the models rather than by the caller: PascalCase schedule rows
  exposed as snake_case attributes, `usageType` on navaids, `le_heading_degT` on runways,
  `from`/`to` query keys for `ml.flight_time(origin=..., destination=...)`, `bbox` tuples
  serialised to a single comma separated string, and per-endpoint date formats
  (`DD-MM-YYYY` for schedules, `YYYY-MM-DD` for tickets, ISO 8601 for history).
- Scraped values are left as served: opaque time strings (flight status, NOTAM
  effective/expiration, FAA delay durations) and numeric-looking strings
  (`Airline.active`, `frequency_mhz`, `year_built`) are never coerced.
- 200-with-a-sentinel responses stay typed values rather than exceptions:
  `AircraftLookup.found`, `AirportsByIPResponse.error`, `HistoryFlightsResponse.note`.

**Errors, retries and timeouts**

- Exception hierarchy: `SkyLinkError` → `APIConnectionError`/`APITimeoutError`,
  `APIResponseValidationError`, and `APIStatusError` → `BadRequestError`,
  `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`,
  `UnprocessableEntityError`, `RateLimitError`, `InternalServerError`,
  `ServiceUnavailableError`. One parser normalises the API's three error body shapes into
  `.message`, keeping `.code`, `.errors` and the raw `.body`.
- Retries on 429/500/502/503/504 and transport failures, default 3, with full-jitter
  backoff (`random() * min(8s, 0.5s * 2 ** attempt)`) and `Retry-After` support (seconds
  or HTTP-date, capped at 60s). `POST` is replayed on 429 only, so a webhook is never
  created twice.
- Default timeouts: connect 5s, read 30s, write 30s, pool 5s. Both timeout and retry
  budget are overridable per call via `request_options`.

**Developer experience** (three more namespaces, one helper package, no new runtime dependency)

- `batch` — `metars`, `tafs`, `notams`, `airports`, `flight_statuses` over many identifiers
  with bounded concurrency (default 5). Returns `{identifier: value | SkyLinkError}` keyed by
  the string you passed, in input order, duplicates collapsed; one failing identifier never
  costs the others. `helpers.batch` has the primitive (`map_concurrent`/`amap_concurrent`) and
  the readers (`successes`, `failures`, `raise_for_errors`).
- `compose` — `airport_brief`, `flight_brief`, `route_brief`, `enrich_adsb`,
  `schedules_with_status`, `north_america_countries`. Sub-requests go out in parallel and a
  part that fails is `None` with its error in `.errors[part]`: the aggregate degrades instead
  of raising, except for the primary request. `include=`/`exclude=` decide what is requested
  at all. `north_america_countries()` works around a backend bug — `continent="NA"` is read as
  NaN server-side, so `geo.countries(continent="NA")` returns nothing.
- `poll` — `poll.adsb()` yields `AdsbDiff(appeared, disappeared, updated, snapshot, is_first)`,
  `poll.flight_status()` yields only real changes and stops on a terminal status. First request
  is immediate, 429/5xx are survived (honouring `Retry-After`), 401/403/422 propagate,
  `max_iterations` bounds the loop and `sleep=` is injectable.
- Iterators on the paged endpoints: `adsb.iter_aircraft()` (limit/offset paging) and
  `history.iter_flights()` (slices a long range into per-plan windows, newest first). Async
  clients return `AsyncIterator`s from the same names.
- `skylink_api.helpers` — pure, network-free modules: `units` (converters plus
  `normalize_altimeter`, `parse_visibility`, `parse_duration_minutes`), `spatial` (`bbox`,
  `bbox_around`, haversine, bearing, `track_stats`, `simplify_track`), `weather`
  (`flight_category`, `ceiling_ft`, `metar_age`, `is_stale`, `wind_components`), `geojson`
  (RFC 7946 exporters, always `[lon, lat]`), `idents`, `sentinels` and `batch`.
- Opt-in response cache: `SkyLink(cache=MemoryCache(ttls={"weather.metar": 60}))`. Off by
  default and inert without TTL rules; successful GETs only; a store that raises degrades to
  no cache with a warning. Any `get`/`set` object satisfies `CacheProtocol`.
- Quota hooks `on_rate_limit(cb)` and `on_quota_low(cb, threshold=0.1)` (edge triggered), both
  returning an unsubscribe callable; a raising hook is a warning, never a failed request.
- `SkyLink.from_env()` and `client.with_options(...)` — the latter clones the client over the
  same connection pool, with the hooks snapshotted and the cache shared.
- Constants `CHART_CATEGORIES`, `WEBHOOK_EVENTS`, `CONTINENTS`, `HISTORY_PLANS`,
  `FLIGHT_CATEGORIES` and the `WebhookEvent` string enum, plus `webhooks.ensure()`
  (`PATCH` only carries `active`, so a changed event list is a delete-then-create).
- `skylink_api.pandas_ext.to_dataframe(obj, *, field=None)` behind the optional
  `skylink-api[pandas]` extra: unwraps the list-shaped envelopes (or a bare list of models or
  dicts) into a `DataFrame`, imports pandas lazily and coerces nothing.

**Packaging and tooling**

- `skylink-api` on PyPI (import `skylink_api`), hatchling build, src layout, Python 3.10+,
  runtime dependencies `httpx>=0.25,<1` and `pydantic>=2.5,<3`. MIT licensed. One optional
  extra, `skylink-api[pandas]`, used only by `skylink_api.pandas_ext`.
- CI on GitHub Actions: ruff, `mypy --strict`, and pytest across Python 3.10-3.13 on Linux
  plus a Windows job. Tag-triggered publishing to PyPI via trusted publishing.
- 1 349 unit tests, network free (`respx` mocks, injected backoff sleeps and pollers), plus an
  environment-gated integration suite (`SKYLINK_TEST_API_KEY` / `SKYLINK_TEST_BASE_URL`,
  RapidAPI channel by default). The pandas tests skip themselves when the extra is absent.
- Eleven runnable scripts in `examples/` and a full method index in the README.

[Unreleased]: https://github.com/skylinkapi/Python-SDK-/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/skylinkapi/Python-SDK-/releases/tag/v0.1.0
