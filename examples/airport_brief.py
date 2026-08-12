"""One command, a whole airport page: ``sky.compose``.

``compose.airport_brief()`` issues the eight requests an airport page needs
(search, METAR, TAF, NOTAMs, FAA delays, charts, departures, arrivals) in
parallel and hands back one object. The important part is what it does when a
part fails: it **degrades instead of raising** — the part is ``None`` and its
error sits in ``brief.errors[part]``. Only the primary request (the airport
lookup itself) is allowed to raise, because without it there is no brief.

Run it with a key in the environment::

    export RAPIDAPI_KEY=...msh...jsn...   # or SKYLINK_API_KEY with provider="direct"
    python examples/airport_brief.py
"""

from __future__ import annotations

from skylink_api import (
    APIStatusError,
    AuthenticationError,
    RateLimitError,
    SkyLink,
)
from skylink_api.helpers.units import parse_duration_minutes
from skylink_api.helpers.weather import ceiling_ft, flight_category
from skylink_api.resources.compose import AIRPORT_BRIEF_PARTS

#: ICAO, not IATA: METAR, TAF, NOTAMs and charts are ICAO-only endpoints.
AIRPORT = "KJFK"

#: A non-US field to show a part failing without taking the brief down: FAA
#: delays only exist for US airports, so `errors["delays"]` is the normal answer.
FOREIGN_AIRPORT = "EGLL"

ROUTE = ("EGLL", "KJFK")


def show_brief(sky: SkyLink, icao: str) -> None:
    """Everything about one airport, in one call."""

    brief = sky.compose.airport_brief(icao, schedules_limit=5)

    airport = brief.airport
    print(f"\n{icao} — {airport.name if airport else '?'}")

    if brief.metar is not None:
        # Weather is fetched with parsed=True, so the derived helpers work.
        print(f"  METAR     {brief.metar.raw}")
        print(
            f"  category  {flight_category(brief.metar) or '?'}, ceiling {ceiling_ft(brief.metar)}"
        )
    if brief.taf is not None:
        periods = len(brief.taf.parsed.forecast) if brief.taf.parsed else 0
        print(f"  TAF       {periods} periods")
    if brief.notams is not None:
        print(f"  NOTAMs    {brief.notams.total} active")
    if brief.charts is not None:
        categories = ", ".join(sorted(brief.charts.charts)) or "none"
        print(f"  charts    {brief.charts.total_count} ({categories})")
    if brief.delays is not None:
        print(f"  delays    {len(brief.delays.ground_stops)} ground stops")
    if brief.departures is not None:
        # total_flights is the count BEFORE schedules_limit truncated the rows.
        rows = brief.departures.flights
        print(f"  departing {brief.departures.total_flights} today, next {len(rows)}:")
        for row in rows:
            print(f"    {row.time or '--'}  {row.flight or '?':8} {row.destination or ''}")

    # Failures never reach the caller as exceptions — they land here.
    for part, error in brief.errors.items():
        print(f"  ! {part}: {type(error).__name__} — {error}")


def show_cheap_brief(sky: SkyLink, icao: str) -> None:
    """``include=`` limits what is requested at all, so it also limits quota use."""

    print(f"\n{icao} — weather only (parts available: {', '.join(AIRPORT_BRIEF_PARTS)})")
    brief = sky.compose.airport_brief(icao, include=("airport", "metar", "taf"))

    print(f"  {brief.metar.raw if brief.metar else 'no observation'}")
    # Parts that were not requested are None with NO entry in errors: "not asked
    # for" and "asked for and failed" stay distinguishable.
    print(f"  notams requested: {brief.notams is not None}, errors: {sorted(brief.errors)}")


def show_route(sky: SkyLink) -> None:
    """The same idea for a city pair — nothing primary, everything parallel."""

    origin, destination = ROUTE
    brief = sky.compose.route_brief(origin, destination)

    print(f"\n{origin} -> {destination}")
    if brief.distance is not None:
        print(f"  distance  {brief.distance.distance} {brief.distance.unit}")
    if brief.flight_time is not None:
        # The model returns English prose ("7h 23m"); the helper makes it a number.
        display = brief.flight_time.estimated_hours_display
        print(f"  block     {display} ({parse_duration_minutes(display)} min)")
    if brief.carbon is not None:
        print(
            f"  CO2       {brief.carbon.co2_kg_total} kg total, "
            f"{brief.carbon.co2_kg_per_passenger} kg per seat"
        )
    for part, error in brief.errors.items():
        print(f"  ! {part}: {type(error).__name__}")


def main() -> None:
    try:
        with SkyLink() as sky:  # RapidAPI channel, api_key falls back to $RAPIDAPI_KEY
            show_brief(sky, AIRPORT)
            show_brief(sky, FOREIGN_AIRPORT)  # expect errors["delays"]: not an FAA field
            show_cheap_brief(sky, AIRPORT)
            show_route(sky)
    except AuthenticationError as err:
        raise SystemExit(f"Auth failed: {err}") from err
    except RateLimitError as err:
        # One brief is eight requests: mind the quota before looping over airports.
        raise SystemExit(f"Quota exhausted, retry in {err.retry_after}s") from err
    except APIStatusError as err:
        # Reached only for the primary request (airports/search); every other
        # failure is inside brief.errors.
        raise SystemExit(f"API error {err.status_code}: {err.message}") from err


if __name__ == "__main__":
    main()
