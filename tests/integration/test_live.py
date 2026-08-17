"""Live smoke tests: one call per namespace against a real deployment.

What is asserted here is **that the round trip works and the response parses
into the declared model** — never the data itself. Live aviation data changes by
the minute: a METAR that exists now may be absent in ten minutes, an ADS-B feed
may be empty at 3 a.m., and a schedule board depends on whoever is flying today.
Asserting on values would make this suite a random number generator.

So each test checks three things:

1. the request reached the API and came back 2xx;
2. the payload validated into the Pydantic model the SDK promises;
3. the envelope's structural invariants hold (counts match list lengths, echoed
   query parameters come back, discriminators are one of the declared values).

Endpoints whose data comes from third parties degrade to ``503`` (upstream down
or cache still warming) or ``404`` (nothing for this identifier right now).
Those are correct API behaviour, so they skip with an explanatory message
instead of failing — see :func:`tests.integration.conftest.tolerating`.

Both channels are worth a run: they differ in more than the host — quota header
names, the shape of a 404 for an unrouted path, and which endpoints the plan
allows (see :mod:`tests.integration.test_live_webhooks`).

Run with::

    # RapidAPI channel (the SDK default)
    SKYLINK_TEST_API_KEY=...msh...jsn... \
        ./.venv/Scripts/python.exe -m pytest tests/integration -v

    # direct channel, with a direct (Polar licence) key
    SKYLINK_TEST_PROVIDER=direct SKYLINK_TEST_API_KEY=... \
        ./.venv/Scripts/python.exe -m pytest tests/integration -v

    # or a staging deployment
    SKYLINK_TEST_BASE_URL=http://localhost:8081/v3.1 \
        ./.venv/Scripts/python.exe -m pytest tests/integration -v
"""

from __future__ import annotations

import pytest

from skylink_api import (
    AdsbAircraftList,
    AdsbStatistics,
    AircraftLookup,
    Airline,
    AirlineRoutesResult,
    AirSigmetResponse,
    AsyncSkyLink,
    CarbonEstimate,
    ChartSourcesResponse,
    ChartsResponse,
    CountriesResponse,
    DeparturesResponse,
    DistanceResponse,
    EnrichedAirport,
    FaaDelayResponse,
    FlightBriefing,
    FlightStatusArrival,
    FlightStatusDeparture,
    FlightStatusResponse,
    FlightTimePrediction,
    HistoryFlightsResponse,
    MetarParsed,
    MetarWithParsed,
    NavaidsResponse,
    NotamsResponse,
    NotFoundError,
    SkyLink,
    TicketSearchResponse,
    VrsRouteResult,
    Webhook,
    WebhookSubscription,
    WebhookToggleResponse,
)

from .conftest import ARMED, BASE_URL, PLAN_ERRORS, PROVIDER, SKIP_REASON, tolerating

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not ARMED, reason=SKIP_REASON),
]

# Stable, busy, well documented reference points. They exist in every dataset
# the API ships; whether they have *live* data at this instant is another matter,
# which is exactly what `tolerating()` absorbs.
ORIGIN = "KJFK"
DESTINATION = "EGLL"
ORIGIN_IATA = "JFK"
DESTINATION_IATA = "LHR"


# ── 1. weather ───────────────────────────────────────────────────────────────


def test_weather_metar_parses(sky: SkyLink) -> None:
    """``GET /weather/metar/{icao}?parsed=true`` → the parsed overload."""

    with tolerating("weather.metar"):
        report = sky.weather.metar(ORIGIN, parsed=True)

        assert isinstance(report, MetarWithParsed)
        assert report.icao == ORIGIN
        # `parsed` is the whole point of the overload: present, even if null.
        assert "parsed" in report.model_dump()
        if report.parsed is not None:
            assert isinstance(report.parsed, MetarParsed)
            assert isinstance(report.parsed.clouds, list)


def test_weather_airsigmet(sky: SkyLink) -> None:
    """``GET /weather/airsigmet?bbox=`` → advisories intersecting the box.

    This used to be ``xfail``: the endpoint answered a bare ``500 Internal Server
    Error`` for every bbox, integer and float alike, while ``/weather/pireps``
    served the same box fine. Fixed backend side and verified live on
    2026-08-15 — 12 SIGMETs over the western US — so the marker is gone and a
    500 is a failure again.
    """

    with tolerating("weather.airsigmet"):
        result = sky.weather.airsigmet(bbox=(30.0, -120.0, 45.0, -100.0))

        assert isinstance(result, AirSigmetResponse)
        assert result.total == len(result.reports)


def test_weather_airsigmet_type_filter_is_echoed(sky: SkyLink) -> None:
    """``type=`` narrows the set and comes back in ``filter_type``.

    The half of the endpoint that a 500 hid completely: unfiltered the response
    carries ``filter_type=None``, and a filtered one echoes the value it applied.
    """

    with tolerating("weather.airsigmet(type=)"):
        bbox = (30.0, -120.0, 45.0, -100.0)
        everything = sky.weather.airsigmet(bbox=bbox)
        sigmets = sky.weather.airsigmet(bbox=bbox, type="sigmet")

        assert everything.filter_type is None
        assert sigmets.filter_type == "sigmet"
        assert sigmets.total == len(sigmets.reports) <= everything.total


# ── 2. airports ──────────────────────────────────────────────────────────────


def test_airports_search_by_icao(sky: SkyLink) -> None:
    """``GET /airports/search?icao=`` → the enriched airport envelope."""

    with tolerating("airports.search"):
        airport = sky.airports.search(icao=ORIGIN)

        assert isinstance(airport, EnrichedAirport)
        assert airport.search_code == ORIGIN
        # The API echoes the search kind upper cased.
        assert airport.search_type == "ICAO"
        # Enrichment lists are always present, even when empty.
        assert isinstance(airport.runways, list)
        assert isinstance(airport.frequencies, list)
        assert isinstance(airport.navaids, list)


# ── 3. airlines ──────────────────────────────────────────────────────────────


def test_airlines_search_by_icao(sky: SkyLink) -> None:
    """``GET /airlines/search?icao=`` → a bare list, not an envelope."""

    with tolerating("airlines.search"):
        airlines = sky.airlines.search(icao="BAW")

        assert isinstance(airlines, list)
        assert all(isinstance(item, Airline) for item in airlines)


# ── 4. navaids ───────────────────────────────────────────────────────────────


def test_navaids_list(sky: SkyLink) -> None:
    """``GET /navaids?country=&limit=`` — at least one filter is mandatory."""

    with tolerating("navaids.list"):
        result = sky.navaids.list(country="US", limit=5)

        assert isinstance(result, NavaidsResponse)
        assert len(result.navaids) <= 5
        # `total` counts matches before the limit, so it may exceed the page.
        assert result.total >= len(result.navaids)
        assert isinstance(result.filters_applied, dict)


# ── 5. geo ───────────────────────────────────────────────────────────────────


def test_geo_countries(sky: SkyLink) -> None:
    """``GET /countries`` → reference data, no upstream involved."""

    with tolerating("geo.countries"):
        result = sky.geo.countries()

        assert isinstance(result, CountriesResponse)
        assert result.total == len(result.countries)
        assert result.countries, "the country dataset should never be empty"


# ── 6. adsb ──────────────────────────────────────────────────────────────────


def test_adsb_aircraft_page_and_statistics(sky: SkyLink) -> None:
    """``GET /adsb/aircraft?limit=`` and ``GET /adsb/statistics``.

    The only paginated endpoint in the API. An empty feed is a valid answer.
    """

    with tolerating("adsb.aircraft"):
        page = sky.adsb.aircraft(limit=5)

        assert isinstance(page, AdsbAircraftList)
        assert len(page.aircraft) <= 5
        assert page.total_count >= len(page.aircraft)

    with tolerating("adsb.statistics"):
        stats = sky.adsb.statistics()

        assert isinstance(stats, AdsbStatistics)
        assert stats.total_aircraft >= stats.positioned_aircraft


# ── 7. aircraft ──────────────────────────────────────────────────────────────


def test_aircraft_by_registration(sky: SkyLink) -> None:
    """``GET /aircraft/registration/{reg}`` → the ``found`` sentinel envelope."""

    with tolerating("aircraft.by_registration"):
        lookup = sky.aircraft.by_registration("G-STBC", photos=False)

        assert isinstance(lookup, AircraftLookup)
        assert isinstance(lookup.found, bool)
        # The two branches of the sentinel must stay consistent with each other.
        assert (lookup.aircraft is not None) == lookup.found


def test_aircraft_miss_is_a_sentinel_not_an_error(sky: SkyLink) -> None:
    """A registration nobody owns answers **200 with ``found: false``**.

    This is the SDK's contract for "not found" sentinels: they are typed fields,
    never exceptions. If this ever raises, the sentinel handling regressed.
    """

    lookup = sky.aircraft.by_registration("ZZ-ZZZZ", photos=False)

    assert isinstance(lookup, AircraftLookup)
    assert lookup.found is False
    assert lookup.aircraft is None


# ── 8. charts ────────────────────────────────────────────────────────────────


def test_charts_sources_and_airport(sky: SkyLink) -> None:
    """``GET /charts/sources`` (static) and ``GET /charts/{icao}`` (scraped)."""

    with tolerating("charts.sources"):
        sources = sky.charts.sources()

        assert isinstance(sources, ChartSourcesResponse)
        assert sources.total_count == len(sources.sources)

    with tolerating("charts.by_airport"):
        charts = sky.charts.by_airport(ORIGIN)

        assert isinstance(charts, ChartsResponse)
        assert charts.icao_code == ORIGIN
        # Categories are keys of a dict, and the count spans all of them.
        assert charts.total_count == sum(len(items) for items in charts.charts.values())


# ── 9. delays ────────────────────────────────────────────────────────────────


def test_delays_faa(sky: SkyLink) -> None:
    """``GET /delays/faa`` — live FAA NAS status."""

    with tolerating("delays.faa"):
        delays = sky.delays.faa()

        assert isinstance(delays, FaaDelayResponse)
        assert delays.total_alerts == (
            len(delays.ground_delays)
            + len(delays.ground_stops)
            + len(delays.closures)
            + len(delays.airspace_flow_programs)
        )


# ── 10. notams ───────────────────────────────────────────────────────────────


def test_notams_by_airport(sky: SkyLink) -> None:
    """``GET /notams/{icao}`` — FAA SWIM feed, 503 while it is unreachable."""

    with tolerating("notams.by_airport"):
        result = sky.notams.by_airport(ORIGIN, exclude_scope=["FIR"])

        assert isinstance(result, NotamsResponse)
        assert result.icao == ORIGIN
        assert result.total == len(result.notams)
        # NOTAM times stay opaque strings — the SDK must not have parsed them.
        for notam in result.notams[:5]:
            assert notam.effective is None or isinstance(notam.effective, str)


# ── 11. schedules ────────────────────────────────────────────────────────────


def test_schedules_departures(sky: SkyLink) -> None:
    """``GET /schedules/departures`` — PascalCase wire keys behind snake_case."""

    with tolerating("schedules.departures"):
        board = sky.schedules.departures(icao=DESTINATION)

        assert isinstance(board, DeparturesResponse)
        assert board.direction == "departures"
        assert board.total_flights == len(board.flights)
        # The alias generator has to have mapped `Time`/`Destination` etc.
        for flight in board.flights[:5]:
            assert flight.time is None or isinstance(flight.time, str)
            assert flight.flight is None or isinstance(flight.flight, str)


# ── 12. ml ───────────────────────────────────────────────────────────────────


def test_ml_flight_time(sky: SkyLink) -> None:
    """``GET /ml/flight-time`` — SDK ``origin``/``destination`` → wire ``from``/``to``.

    A wrong alias mapping surfaces here as a 422, so this call doubles as a
    regression test for the query builder.
    """

    with tolerating("ml.flight_time"):
        prediction = sky.ml.flight_time(origin=ORIGIN, destination=DESTINATION)

        assert isinstance(prediction, FlightTimePrediction)
        assert prediction.origin == ORIGIN
        assert prediction.destination == DESTINATION
        assert prediction.min_minutes <= prediction.estimated_minutes <= prediction.max_minutes


# ── 13. carbon ───────────────────────────────────────────────────────────────


def test_carbon_estimate(sky: SkyLink) -> None:
    """``GET /carbon/estimate``."""

    with tolerating("carbon.estimate"):
        estimate = sky.carbon.estimate(
            departure_icao=ORIGIN,
            arrival_icao=DESTINATION,
            aircraft_type="B77W",
            passengers=250,
        )

        assert isinstance(estimate, CarbonEstimate)
        assert estimate.departure_icao == ORIGIN
        assert estimate.arrival_icao == DESTINATION
        assert estimate.passengers == 250


# ── 14. briefing ─────────────────────────────────────────────────────────────


def test_briefing_flight_json(sky: SkyLink) -> None:
    """``GET /briefing/flight?format=json`` → the structured overload."""

    with tolerating("briefing.flight"):
        briefing = sky.briefing.flight(origin=ORIGIN, destination=DESTINATION)

        assert isinstance(briefing, FlightBriefing)
        assert briefing.origin == ORIGIN
        assert briefing.destination == DESTINATION
        assert isinstance(briefing.data_included, list)


def test_briefing_flight_markdown_is_text(sky: SkyLink) -> None:
    """The same endpoint with ``format="markdown"`` resolves to a plain string."""

    with tolerating("briefing.flight(format=markdown)"):
        rendered = sky.briefing.flight(
            origin=ORIGIN,
            destination=DESTINATION,
            format="markdown",
        )

        assert isinstance(rendered, str)
        assert rendered.strip(), "a rendered briefing should not be empty"


def test_briefing_pdf_magic_bytes(sky: SkyLink) -> None:
    """``GET /briefing/pdf`` — the only binary endpoint in the API.

    ``%PDF`` in the first four bytes proves the whole binary path: no JSON
    decoding, no text decoding, no re-encoding.
    """

    with tolerating("briefing.pdf"):
        payload = sky.briefing.pdf(departure_icao=ORIGIN, arrival_icao=DESTINATION)

        assert isinstance(payload, bytes)
        assert payload[:4] == b"%PDF", f"not a PDF, starts with {payload[:8]!r}"
        assert len(payload) > 1000, "a briefing PDF should be more than a header"


# ── 15. routes ───────────────────────────────────────────────────────────────


def test_routes_by_callsign_union(sky: SkyLink) -> None:
    """``GET /routes/callsign/{cs}`` → union discriminated by ``source``."""

    with tolerating("routes.by_callsign"):
        route = sky.routes.by_callsign("BAW117")

        assert isinstance(route, VrsRouteResult | AirlineRoutesResult)
        if isinstance(route, VrsRouteResult):
            assert route.source == "vrs"
            assert isinstance(route.airports, list)
        else:
            assert route.source == "airline_routes"
            assert isinstance(route.routes, list)


# ── 16. tickets ──────────────────────────────────────────────────────────────


def test_tickets_search(sky: SkyLink) -> None:
    """``GET /tickets/search`` — scraped pricing, frequently 503."""

    with tolerating("tickets.search"):
        result = sky.tickets.search(
            origin=ORIGIN_IATA,
            destination=DESTINATION_IATA,
            passengers=1,
        )

        assert isinstance(result, TicketSearchResponse)
        assert result.origin == ORIGIN_IATA
        assert result.destination == DESTINATION_IATA
        assert result.count == len(result.flights)


# ── 17. webhooks ─────────────────────────────────────────────────────────────


def test_webhooks_full_crud_cycle(sky: SkyLink) -> None:
    """create → list → update → delete, with guaranteed cleanup.

    ``try/finally`` matters here: a failed assertion halfway through must not
    leave a live subscription behind on the account.
    """

    with tolerating("webhooks CRUD", errors=PLAN_ERRORS):
        # GET /webhooks/events — the values the SDK's Literal is built from.
        event_types = sky.webhooks.event_types()
        assert isinstance(event_types, list)
        assert all(isinstance(name, str) for name in event_types)
        assert "status_changed" in event_types

        # POST /webhooks → 201
        created = sky.webhooks.create(
            url="https://example.com/skylink-sdk-integration",
            event_types=["status_changed"],
            filters={"flight_number": "BA117"},
        )
        assert isinstance(created, Webhook)
        assert created.id, "the API must return the subscription id"
        webhook_id = created.id

        try:
            listed = sky.webhooks.list()
            assert isinstance(listed, list)
            assert all(isinstance(item, WebhookSubscription) for item in listed)
            assert webhook_id in {item.id for item in listed}

            toggled = sky.webhooks.update(webhook_id, active=False)
            assert isinstance(toggled, WebhookToggleResponse)
            assert toggled.id == webhook_id
            assert toggled.active is False
        finally:
            # Runs even when an assertion above blew up, so the account is never
            # left with a stray subscription. DELETE answers 204 No Content and
            # the SDK returns None for it, rather than failing to decode an
            # empty body — reaching the next line at all is that proof.
            sky.webhooks.delete(webhook_id)

        assert webhook_id not in {item.id for item in sky.webhooks.list()}


# ── 18. history ──────────────────────────────────────────────────────────────


def test_history_flights(sky: SkyLink) -> None:
    """``GET /{plan}/history/flights`` — needs TimescaleDB, else 503.

    A filter is mandatory: the endpoint rejects an unfiltered search with 422,
    and the SDK refuses it client side.
    """

    with tolerating("history.flights"):
        result = sky.history.flights(departure_icao=ORIGIN, limit=5)

        assert isinstance(result, HistoryFlightsResponse)
        assert result.count == len(result.flights)
        assert len(result.flights) <= 5
        # `note` is a 200 sentinel ("no data for this filter"), not an error.
        assert result.note is None or isinstance(result.note, str)


# ── client level shortcut methods ────────────────────────────────────────────


def test_flight_status_shortcut(sky: SkyLink) -> None:
    """``sky.flight_status(...)`` — a method on the client, not a namespace."""

    with tolerating("sky.flight_status"):
        status = sky.flight_status("BA117")

        assert isinstance(status, FlightStatusResponse)
        # Scraped payload: every field is an optional string, and the two legs
        # are deliberately different shapes (`checkin` vs `baggage`).
        assert status.flight_number is None or isinstance(status.flight_number, str)
        if status.departure is not None:
            assert isinstance(status.departure, FlightStatusDeparture)
        if status.arrival is not None:
            assert isinstance(status.arrival, FlightStatusArrival)


def test_distance_shortcut(sky: SkyLink) -> None:
    """``sky.distance(...)`` — the other client level shortcut."""

    with tolerating("sky.distance"):
        leg = sky.distance(from_icao=ORIGIN, to_icao=DESTINATION, unit="km")

        assert isinstance(leg, DistanceResponse)
        assert leg.unit == "km"
        assert leg.distance is not None and leg.distance > 0
        assert leg.bearing is not None and 0 <= leg.bearing <= 360
        assert leg.midpoint is not None


# ── async client ─────────────────────────────────────────────────────────────


async def test_async_client_smoke(async_sky: AsyncSkyLink) -> None:
    """The async client shares the builders, so one round trip is enough proof."""

    with tolerating("async geo.countries"):
        result = await async_sky.geo.countries()

        assert isinstance(result, CountriesResponse)
        assert result.total == len(result.countries)


# ── channel behaviour ────────────────────────────────────────────────────────


def test_unknown_endpoint_is_a_parsed_not_found(sky: SkyLink) -> None:
    """An unrouted path is a ``NotFoundError`` with a real message on either channel.

    The channels disagree about *who* answers and in what words — the direct
    gateway proxies through to FastAPI (``{"detail": "Not Found"}``), while the
    RapidAPI edge rejects the path itself before the backend sees it
    (``{"message": "Endpoint '/...' does not exist"}``, and no quota is spent).
    The SDK has to normalise both, so the assertion is that the message survived
    rather than degrading to the bare ``HTTP 404`` fallback.
    """

    with pytest.raises(NotFoundError) as excinfo:
        sky.request("GET", "/there-is-no-such-endpoint")

    error = excinfo.value
    assert error.status_code == 404
    assert error.message and error.message != "HTTP 404", (
        f"the 404 body was not parsed into a message: {error.body!r}"
    )


# ── rate limit headers ───────────────────────────────────────────────────────


def test_rate_limit_headers_are_captured(sky: SkyLink) -> None:
    """``client.last_rate_limit`` reflects whatever quota headers the channel sends.

    The two channels spell them differently — ``X-RateLimit-Requests-*`` on
    RapidAPI, ``X-RateLimit-*`` on the direct gateway (and on the wire the direct
    one arrives as ``X-Ratelimit-Limit``) — so a parser that knows only one of
    them leaves ``last_rate_limit`` at ``None`` on the other channel, and with it
    both quota hooks silently dead. Against a production channel the attribute is
    therefore **required**, not merely tolerated; only a custom ``base_url``
    (staging behind neither gateway) is allowed to send nothing.
    """

    with tolerating("geo.countries (quota headers)"):
        sky.geo.countries()

    quota = sky.last_rate_limit

    if BASE_URL:
        # Staging with ``DISABLE_AUTH=true`` injects no quota headers at all; what
        # must never happen is a crash while reading the attribute.
        if quota is not None:
            assert quota.limit is None or quota.limit >= 0
            assert quota.remaining is None or quota.remaining >= 0
        return

    assert quota is not None, (
        f"the {PROVIDER} gateway sends quota headers on every response; "
        "last_rate_limit staying None means the SDK did not recognise them"
    )
    assert quota.limit is not None and quota.limit > 0
    assert quota.remaining is not None and 0 <= quota.remaining <= quota.limit
    assert quota.reset is not None and quota.reset >= 0
