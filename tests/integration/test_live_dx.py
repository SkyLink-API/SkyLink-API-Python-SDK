"""Live tests for the convenience layer: batch, compose, poll, cache, helpers.

:mod:`tests.integration.test_live` proves that every *endpoint* round trips and
parses. This file proves the layer built **on top** of those endpoints — the
fan-outs, the aggregates, the pagination driver, the cache and the pure
helpers — against the same real deployment, because all of it was written
against mocks and mocks agree with whatever the author believed.

The assertions are about behaviour that must hold whatever the live data says:

* a batch keeps the caller's keys and one bad identifier loses nothing else;
* a brief degrades part by part, and a failed part is a typed error in
  ``errors`` rather than an exception;
* ``iter_aircraft`` really advances its offset (no repeated addresses);
* a cache hit really skips the network;
* ``poll.adsb`` diffs two consecutive snapshots of a moving feed.

Data-dependent facts (which parts of an airport brief have data right now, how
many aircraft are in a box over England) are printed, not asserted — run with
``-s`` to read them.

Run with::

    SKYLINK_TEST_PROVIDER=rapidapi SKYLINK_TEST_API_KEY=...msh...jsn... \
        ./.venv/Scripts/python.exe -m pytest tests/integration/test_live_dx.py -rs -v
"""

from __future__ import annotations

from typing import Any

import pytest

from skylink_api import (
    AdsbAircraftList,
    AircraftLookup,
    AirlineRoutesResult,
    APIStatusError,
    CarbonEstimate,
    DistanceResponse,
    EnrichedAirport,
    FlightStatusResponse,
    FlightTimePrediction,
    Metar,
    MetarWithParsed,
    RateLimitInfo,
    SkyLink,
    SkyLinkError,
    VrsRouteResult,
)
from skylink_api.helpers import (
    bbox_around,
    failures,
    flight_category,
    normalize_altimeter,
    successes,
)
from skylink_api.helpers.cache import MemoryCache
from skylink_api.helpers.geojson import adsb_to_geojson
from skylink_api.models.compose import EnrichedAircraft
from skylink_api.resources.compose import AIRPORT_BRIEF_PARTS, ROUTE_BRIEF_PARTS

from .conftest import (
    API_KEY,
    ARMED,
    BASE_URL,
    PROVIDER,
    SKIP_REASON,
    describe,
    tolerating,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not ARMED, reason=SKIP_REASON),
]

ORIGIN = "KJFK"
DESTINATION = "EGLL"
THIRD = "UUEE"
#: A syntactically valid ICAO code that no airport uses — a guaranteed 404.
MISSING = "ZZZZ"

#: Heathrow, and a box big enough to hold traffic at any hour.
LONDON_LAT = 51.4775
LONDON_LON = -0.4614
LONDON_RADIUS_KM = 120.0


def _report(title: str, *lines: object) -> None:
    """Print a labelled block of live findings (visible under ``pytest -s``)."""

    print(f"\n[live] {title}")
    for line in lines:
        print(f"       {line}")


def _describe(error: SkyLinkError) -> str:
    """``describe()`` for an HTTP failure, the message for a transport one."""

    return (
        describe(error) if isinstance(error, APIStatusError) else f"{type(error).__name__}: {error}"
    )


def _errors_of(brief: Any) -> str:
    """One-line rendering of a brief's ``errors`` mapping."""

    if not brief.errors:
        return "errors: {} (every requested part answered)"
    parts = ", ".join(f"{name}={_describe(error)}" for name, error in brief.errors.items())
    return f"errors: {parts}"


def _london_box() -> str:
    return bbox_around(LONDON_LAT, LONDON_LON, LONDON_RADIUS_KM)


# ── 1. batch ─────────────────────────────────────────────────────────────────


def test_batch_metars_keeps_every_key_and_isolates_the_failure(sky: SkyLink) -> None:
    """``sky.batch.metars`` over three real airports plus one that cannot exist.

    Three properties, all of them the point of the namespace:

    1. the keys are the strings that were passed, verbatim (lower case stays
       lower case — the SDK does not normalise them behind your back);
    2. one identifier failing does not lose the others;
    3. the failure is a value in the mapping, not a raised exception.
    """

    codes = [ORIGIN, DESTINATION, THIRD, MISSING]
    reports = sky.batch.metars(codes)

    assert list(reports) == codes, "keys must be the input strings, in input order"

    ok = successes(reports)
    bad = failures(reports)
    _report(
        "batch.metars",
        f"requested {codes}",
        f"parsed: {sorted(ok)}",
        *(f"failed: {code} -> {_describe(error)}" for code, error in bad.items()),
    )

    # The impossible code is an error *value*; the real ones are unaffected by it.
    assert isinstance(reports[MISSING], SkyLinkError)
    assert all(isinstance(report, Metar) for report in ok.values())
    if not ok:
        pytest.skip(f"every station was unavailable: {bad}")
    assert set(ok) <= {ORIGIN, DESTINATION, THIRD}


def test_batch_collapses_duplicates_and_keeps_the_case_it_was_given(sky: SkyLink) -> None:
    """``["kjfk", "KJFK", "kjfk"]`` is two keys and two requests, not three."""

    reports = sky.batch.metars(["kjfk", ORIGIN, "kjfk"])

    assert list(reports) == ["kjfk", ORIGIN]


# ── 2. compose.airport_brief ─────────────────────────────────────────────────


def test_compose_airport_brief_reports_which_parts_are_alive(sky: SkyLink) -> None:
    """Eight endpoints in one call — what came back, and what is in ``errors``.

    The airport itself is the primary part and is asserted; every other part is
    printed rather than asserted, because whether KJFK has a TAF or FAA delays
    right now is not a property of this SDK.
    """

    with tolerating("compose.airport_brief (primary part)"):
        brief = sky.compose.airport_brief(ORIGIN, schedules_limit=3)

        present = [name for name in AIRPORT_BRIEF_PARTS if getattr(brief, name) is not None]
        _report(
            "compose.airport_brief('KJFK')",
            f"parts with data: {present}",
            _errors_of(brief),
        )

        assert isinstance(brief.airport, EnrichedAirport)
        assert brief.airport.search_code == ORIGIN
        # Failures are collected under their own part name, never raised.
        assert set(brief.errors) <= set(AIRPORT_BRIEF_PARTS)
        assert all(isinstance(error, SkyLinkError) for error in brief.errors.values())
        # A part is either data or an error, never both.
        assert not [name for name in brief.errors if getattr(brief, name) is not None]
        # schedules_limit is applied client side; total_flights keeps the real count.
        if brief.departures is not None:
            kept = len(brief.departures.flights)
            assert kept <= 3
            total = brief.departures.total_flights
            assert total is None or total >= kept


def test_compose_airport_brief_include_costs_no_quota_for_the_rest(sky: SkyLink) -> None:
    """``include=`` decides what is *requested*, so unwanted parts are free."""

    calls: list[RateLimitInfo] = []
    unsubscribe = sky.on_rate_limit(calls.append)
    try:
        with tolerating("compose.airport_brief(include=...)"):
            brief = sky.compose.airport_brief(ORIGIN, include=("airport", "metar"))
    finally:
        unsubscribe()

    assert brief.notams is None and brief.charts is None
    assert "notams" not in brief.errors and "charts" not in brief.errors
    if calls:  # No quota headers on a staging instance — then there is nothing to count.
        # Two parts, so two requests — plus any retry, which also carries headers.
        # The claim being tested is that the other six were never asked for.
        assert 2 <= len(calls) < len(AIRPORT_BRIEF_PARTS), (
            f"expected ~2 requests for 2 parts, saw {len(calls)}"
        )


# ── 3. compose.flight_brief ──────────────────────────────────────────────────


def _live_flight_number(sky: SkyLink) -> str:
    """A flight number off a real departure board, so the status exists today."""

    board = sky.schedules.departures(icao=ORIGIN)
    for row in board.flights:
        if row.flight and row.flight.strip("-. "):
            return row.flight.strip()
    pytest.skip("the departure board carried no flight numbers")


def test_compose_flight_brief_on_a_flight_that_is_airborne_now(sky: SkyLink) -> None:
    """A number taken from a live board, so ``status`` is real data, not a 404."""

    with tolerating("schedules.departures (to pick a flight)"):
        number = _live_flight_number(sky)

    with tolerating(f"compose.flight_brief({number})"):
        brief = sky.compose.flight_brief(number)

        _report(
            f"compose.flight_brief({number!r})",
            f"status: {brief.status.status if brief.status else None}",
            f"aircraft: {type(brief.aircraft).__name__}",
            f"route: {type(brief.route).__name__} source={getattr(brief.route, 'source', None)}",
            f"carbon: {type(brief.carbon).__name__}",
            _errors_of(brief),
        )

        assert isinstance(brief.status, FlightStatusResponse)
        assert all(isinstance(error, SkyLinkError) for error in brief.errors.values())
        # No registration on the payload means no lookup was made — that is data
        # missing upstream, not a failure, so it must not be in `errors`.
        if brief.aircraft is None:
            assert isinstance(brief.errors.get("aircraft", None), SkyLinkError | type(None))
        else:
            assert isinstance(brief.aircraft, AircraftLookup)
        if brief.route is not None:
            assert isinstance(brief.route, VrsRouteResult | AirlineRoutesResult)


def test_compose_flight_brief_honours_an_icao_callsign(sky: SkyLink) -> None:
    """Regression, found on this API: ``BAW117`` must not be downgraded to ``BA117``.

    ``/flight_status`` echoes the designator in its **IATA** form, so preferring
    the payload's ``flight_number`` (which is what the SDK used to do) sent
    ``BA117`` to ``/routes/callsign`` — the airline-level fallback, with no
    airport pair, which then made the CO₂ estimate a 404. Passing the ICAO
    callsign now reaches the exact ``"vrs"`` route and prices the leg.
    """

    with tolerating("compose.flight_brief('BAW117')"):
        brief = sky.compose.flight_brief("BAW117")

        _report(
            "compose.flight_brief('BAW117')",
            f"status.flight_number: {brief.status.flight_number if brief.status else None}",
            f"route: {type(brief.route).__name__} "
            f"source={getattr(brief.route, 'source', None)} "
            f"{getattr(brief.route, 'departure_icao', None)}->"
            f"{getattr(brief.route, 'arrival_icao', None)}",
            f"carbon: {type(brief.carbon).__name__}",
            _errors_of(brief),
        )

        if brief.route is None:
            pytest.skip(f"routes/callsign unavailable: {brief.errors.get('route')}")
        assert isinstance(brief.route, VrsRouteResult), (
            "the ICAO callsign should reach the exact VRS route, not the airline fallback"
        )
        # The VRS pair is what prices the carbon; a callsign estimate has neither.
        if brief.carbon is not None:
            assert isinstance(brief.carbon, CarbonEstimate)
            assert brief.carbon.departure_icao == brief.route.departure_icao


# ── 4. compose.route_brief ───────────────────────────────────────────────────


def test_compose_route_brief_kjfk_egll(sky: SkyLink) -> None:
    """Seven independent parts, nothing primary — failures are collected, not raised."""

    brief = sky.compose.route_brief(ORIGIN, DESTINATION, aircraft_type="B77W", passengers=250)

    present = [name for name in ROUTE_BRIEF_PARTS if getattr(brief, name) is not None]
    _report(
        "compose.route_brief('KJFK', 'EGLL')",
        f"parts with data: {present}",
        _errors_of(brief),
    )

    assert set(brief.errors) <= set(ROUTE_BRIEF_PARTS)
    assert all(isinstance(error, SkyLinkError) for error in brief.errors.values())
    if brief.distance is not None:
        assert isinstance(brief.distance, DistanceResponse)
        assert brief.distance.distance is not None and brief.distance.distance > 0
    if brief.flight_time is not None:
        assert isinstance(brief.flight_time, FlightTimePrediction)
        assert brief.flight_time.origin == ORIGIN
    if brief.carbon is not None:
        assert isinstance(brief.carbon, CarbonEstimate)
        assert brief.carbon.passengers == 250
    if not present:
        pytest.skip(f"every part of the route brief was unavailable: {brief.errors}")


# ── 5. compose.enrich_adsb ───────────────────────────────────────────────────


def test_compose_enrich_adsb_joins_the_registry_within_its_budget(sky: SkyLink) -> None:
    """Live contacts joined with the airframe registry, capped at ``max_lookups``.

    The cap is the whole safety story of this method: a bbox over England
    returns hundreds of aircraft and an uncapped join would spend a day's quota
    in one call. Rows past the budget come back with ``info=None`` and **no**
    error, because nothing was requested for them.
    """

    with tolerating("adsb.aircraft (for the join)"):
        page = sky.adsb.aircraft(bbox=_london_box(), limit=8)

    if not page.aircraft:
        pytest.skip("the ADS-B feed returned no aircraft in the box right now")

    budget = 3
    enriched = sky.compose.enrich_adsb(page.aircraft, max_lookups=budget, concurrency=3)

    assert len(enriched) == len(page.aircraft), "one row in, one row out, in input order"
    assert all(isinstance(row, EnrichedAircraft) for row in enriched)

    touched = {
        (row.state.icao24 or "").lower()
        for row in enriched
        if row.info is not None or row.error is not None
    }
    _report(
        "compose.enrich_adsb",
        f"{len(enriched)} contacts, max_lookups={budget}, looked up {len(touched)}",
        *(
            f"{row.state.icao24} {row.state.callsign}: "
            f"found={getattr(row.info, 'found', None)} "
            f"{getattr(getattr(row.info, 'aircraft', None), 'type_name', '')}"
            for row in enriched[:5]
        ),
    )

    assert len(touched) <= budget, "the lookup budget was exceeded"
    for row in enriched:
        assert row.state is not None
        if row.info is not None:
            assert isinstance(row.info, AircraftLookup)
            # The 200 sentinel is data, not an error, and both must never coexist.
            assert row.error is None


# ── 6. compose.north_america_countries ───────────────────────────────────────


def test_compose_north_america_agrees_with_the_server_side_filter(sky: SkyLink) -> None:
    """Both routes to North America now return the same countries.

    ``geo.countries(continent="NA")`` used to return nothing: pandas read the
    literal ``NA`` in the reference CSV as *not-a-number*, so every North
    American country arrived with ``continent: null`` and the server-side filter
    matched zero rows. ``compose.north_america_countries()`` existed to work
    around exactly that by downloading everything and filtering client side.

    The backend was fixed, so the interesting assertion flipped: the two must now
    agree. If the filter regresses to zero rows this fails here, while the
    compose method — which still accepts the historical ``null``/``""`` spellings
    — keeps answering correctly for callers.
    """

    countries = sky.compose.north_america_countries()
    codes = {country.code for country in countries if country.code}

    filtered = sky.geo.countries(continent="NA")
    filtered_codes = {country.code for country in filtered.countries if country.code}

    _report(
        "compose.north_america_countries",
        f"{len(countries)} countries, e.g. {sorted(codes)[:8]}",
        f"geo.countries(continent='NA') returns total={filtered.total} "
        "(the pandas NA-as-NaN defect is fixed)",
    )

    assert 35 <= len(countries) <= 60, (
        f"expected ~41 North American countries, got {len(countries)}"
    )
    assert {"US", "CA", "MX"} <= codes
    # None of them is in Europe or South America — the predicate is not a pass-through.
    assert "GB" not in codes and "BR" not in codes

    assert filtered.total == len(filtered.countries)
    assert filtered_codes == codes, (
        "the server-side continent filter and the client-side one disagree: "
        f"only-server={sorted(filtered_codes - codes)} only-client={sorted(codes - filtered_codes)}"
    )


# ── 7. adsb.iter_aircraft ────────────────────────────────────────────────────


def test_adsb_iter_aircraft_really_pages(sky: SkyLink) -> None:
    """``max_items=150`` over ``page_size=50`` — three requests, no repeats, no loop.

    Repeated addresses would mean ``offset`` was not advancing, which is how a
    pagination driver turns into an infinite loop that reads page 1 forever.
    """

    requests: list[RateLimitInfo] = []
    unsubscribe = sky.on_rate_limit(requests.append)
    try:
        with tolerating("adsb.iter_aircraft"):
            rows = list(sky.adsb.iter_aircraft(max_items=150, page_size=50))
    finally:
        unsubscribe()

    addresses = [(row.icao24 or "").lower() for row in rows if row.icao24]
    _report(
        "adsb.iter_aircraft(max_items=150, page_size=50)",
        f"{len(rows)} aircraft, {len(set(addresses))} distinct addresses, {len(requests)} requests",
    )

    assert len(rows) <= 150, "the iterator overran max_items"
    if len(rows) < 150:
        pytest.skip(f"the live feed held only {len(rows)} aircraft — nothing to page through")

    assert len(set(addresses)) == len(addresses), "a page was re-read: offset is not advancing"
    if requests:  # Quota headers present → they count the pages directly.
        # Three pages of 50; a retried page would add one, one request for 150 rows
        # would mean `page_size` was ignored.
        assert 3 <= len(requests) <= 5, f"expected 3 pages of 50, saw {len(requests)} requests"


# ── 8. helpers on live payloads ──────────────────────────────────────────────


def test_weather_helpers_read_a_live_metar(sky: SkyLink) -> None:
    """``flight_category`` and ``normalize_altimeter`` against a real observation.

    The altimeter is the interesting one: the API sends the number **without a
    unit**, so a US report (``29.92``) and a European one (``1013``) are only
    told apart by magnitude.
    """

    with tolerating("weather.metar(parsed=True)"):
        report = sky.weather.metar(ORIGIN, parsed=True)

    assert isinstance(report, MetarWithParsed)
    if report.parsed is None:
        pytest.skip("the station sent no decoded block")

    category = flight_category(report)
    altimeter = normalize_altimeter(report.parsed.altimeter)
    _report(
        "weather helpers",
        f"raw: {(report.raw or '')[:72]}",
        f"flight_category: {category}",
        f"altimeter {report.parsed.altimeter!r} -> {altimeter}",
    )

    assert category in ("VFR", "MVFR", "IFR", "LIFR", None)
    if report.parsed.altimeter is not None and altimeter is not None:
        assert altimeter.unit in ("inHg", "hPa")
        assert 900 < altimeter.hpa < 1100, "a plausible sea level pressure"
        assert 26 < altimeter.in_hg < 33


def test_bbox_around_feeds_adsb_and_geojson_exports_it(sky: SkyLink) -> None:
    """``bbox_around`` → ``adsb.aircraft`` → ``adsb_to_geojson``, on live traffic.

    The box is a square in kilometres (so a rectangle in degrees), and the
    endpoint must accept the string it produces verbatim — a wrong corner order
    is a 422 here, not a silent empty page.
    """

    box = _london_box()
    with tolerating("adsb.aircraft(bbox=bbox_around(...))"):
        page = sky.adsb.aircraft(bbox=box, limit=10)

    assert isinstance(page, AdsbAircraftList)
    collection = adsb_to_geojson(page)

    _report(
        "bbox_around + adsb + geojson",
        f"box: {box}",
        f"{len(page.aircraft)} of {page.total_count} aircraft in the box",
        f"geojson: {len(collection['features'])} features",
        *(f"feature: {feature['properties']}" for feature in collection["features"][:1]),
    )

    assert collection["type"] == "FeatureCollection"
    # Aircraft without a position are skipped rather than plotted at (0, 0).
    positioned = [row for row in page.aircraft if row.latitude is not None]
    assert len(collection["features"]) == len(positioned)

    south, west, north, east = (float(value) for value in box.split(","))
    for feature in collection["features"]:
        lon, lat = feature["geometry"]["coordinates"]
        assert feature["geometry"]["type"] == "Point"
        assert south - 0.5 <= lat <= north + 0.5
        assert west - 0.5 <= lon <= east + 0.5


# ── 9. cache ─────────────────────────────────────────────────────────────────


def test_memory_cache_serves_the_second_call_without_a_request() -> None:
    """A cache hit must not reach the network — proven by the quota hook.

    Every response fires ``on_rate_limit`` (RapidAPI always sends
    ``X-RateLimit-Requests-*``), so a second identical call that fires nothing
    made no request. The remaining quota not moving is the same statement from
    the API's side.
    """

    cache = MemoryCache(ttls={"geo.*": 300})
    with SkyLink(
        provider=PROVIDER,
        base_url=BASE_URL or None,
        api_key=API_KEY or None,
        environ={},
        cache=cache,
    ) as sky:
        seen: list[RateLimitInfo] = []
        sky.on_rate_limit(seen.append)

        with tolerating("geo.countries (cached)"):
            first = sky.geo.countries()

        if not seen:
            pytest.skip("this deployment sends no quota headers — nothing to count requests with")

        # Whatever the first call cost (a retry would make it more than one
        # response), the second one must cost nothing at all.
        before = len(seen)
        second = sky.geo.countries()

        _report(
            "MemoryCache",
            f"requests for the first call: {before}, for the cached one: {len(seen) - before}",
            f"quota remaining seen: {[info.remaining for info in seen]}",
            f"cache entries: {len(cache)}",
        )

        assert len(seen) == before, "the second call went to the network"
        assert len(cache) == 1
        assert second.total == first.total
        # The cache stores the raw body, so a hit is a fresh model, not the same one.
        assert second is not first

        cache.clear()
        sky.geo.countries()
        assert len(seen) > before, "clearing the cache must send the request again"


# ── 10. poll ─────────────────────────────────────────────────────────────────


def test_poll_adsb_diffs_two_snapshots_of_a_moving_feed(sky: SkyLink) -> None:
    """Two iterations over the live feed: the first is everything, the second is the delta.

    ``max_iterations`` is a hard cap on **requests**, so this costs exactly two
    of them however the feed behaves.
    """

    box = _london_box()
    with tolerating("poll.adsb"):
        diffs = list(sky.poll.adsb(bbox=box, limit=30, interval=3.0, max_iterations=2))

    assert len(diffs) == 2, "max_iterations must bound the loop exactly"
    first, second = diffs

    _report(
        "poll.adsb(max_iterations=2, interval=3)",
        f"first: is_first={first.is_first} snapshot={len(first.snapshot)} "
        f"appeared={len(first.appeared)}",
        f"second: is_first={second.is_first} appeared={len(second.appeared)} "
        f"disappeared={len(second.disappeared)} updated={len(second.updated)}",
    )

    assert first.is_first is True
    assert len(first.appeared) == len(first.snapshot)
    assert not first.disappeared and not first.updated
    assert second.is_first is False
    # Snapshots are keyed by lower-cased address; responses upper-case them, so a
    # regression here would report the whole feed as appeared on every tick.
    assert all(key == key.lower() for key in second.snapshot)
    # An aircraft already in the first snapshot may be unchanged or updated, but
    # never "appeared" — that would mean the two snapshots were keyed differently.
    appeared = {(row.icao24 or "").lower() for row in second.appeared}
    assert not (appeared & set(first.snapshot))
