"""Fan one call out over many identifiers with ``sky.batch``.

The API is one-identifier-per-request: a dashboard of twelve airports means
twelve METAR calls. ``sky.batch`` runs them with bounded concurrency, collapses
duplicates and keeps a bad identifier from losing the good answers — the result
is a ``{identifier: value | SkyLinkError}`` mapping, never an exception.

Run it with a key in the environment::

    export RAPIDAPI_KEY=...msh...jsn...   # or SKYLINK_API_KEY with provider="direct"
    python examples/batch_requests.py
"""

from __future__ import annotations

from skylink_api import (
    APIStatusError,
    AuthenticationError,
    RateLimitError,
    SkyLink,
    SkyLinkError,
)
from skylink_api.helpers.batch import failures, successes
from skylink_api.helpers.idents import is_local_pseudocode

#: ZZZZ is deliberately bogus — it shows that one bad code costs one value, not
#: the whole batch.
NETWORK = ["EGLL", "KJFK", "LFPG", "EDDF", "ZZZZ"]

#: ICAO and IATA mixed freely; batch.airports() picks `icao=` or `iata=` from the
#: shape of each code. "GB-0888" is an OurAirports pseudo-code, which that
#: endpoint genuinely cannot resolve — filter those out before spending a call.
DESTINATIONS = ["EGLL", "JFK", "CDG", "GB-0888"]

FLIGHTS = ["BA117", "AF1680", "LH400"]


def show_metars(sky: SkyLink) -> None:
    """Five METARs, at most three requests in flight."""

    reports = sky.batch.metars(NETWORK, concurrency=3)

    print("METARs")
    for icao, report in reports.items():
        # The keys are the strings that went in, in input order — errors are
        # values, so `reports["ZZZZ"]` exists and holds a NotFoundError.
        if isinstance(report, SkyLinkError):
            print(f"  {icao}: unavailable ({type(report).__name__})")
            continue
        print(f"  {icao}: {report.raw}")

    ok = successes(reports)
    bad = failures(reports)
    print(f"  -> {len(ok)} reports, {len(bad)} failures: {', '.join(bad) or 'none'}")


def show_notams(sky: SkyLink) -> None:
    """Active NOTAM counts for the same network."""

    # Duplicates are requested once and share the answer; the repeated "EGLL"
    # below costs nothing.
    notams = sky.batch.notams(["EGLL", "KJFK", "EGLL"])

    print("\nNOTAMs")
    for icao, response in notams.items():
        if isinstance(response, SkyLinkError):
            print(f"  {icao}: unavailable ({type(response).__name__})")
            continue
        # An airport with nothing active answers 200 with an empty list — data,
        # not an error.
        print(f"  {icao}: {response.total} active")

    # helpers.batch.raise_for_errors(notams) would turn the first failure into an
    # exception instead, for pipelines where a partial answer is not acceptable.


def show_airports(sky: SkyLink) -> None:
    """Enriched airport records for a mix of code flavours."""

    codes = [code for code in DESTINATIONS if not is_local_pseudocode(code)]
    skipped = set(DESTINATIONS) - set(codes)
    if skipped:
        print(f"\nSkipping pseudo-codes (not resolvable by /airports/search): {sorted(skipped)}")

    airports = sky.batch.airports(codes)

    print("Airports")
    for code, airport in airports.items():
        if isinstance(airport, SkyLinkError):
            print(f"  {code}: {airport}")
            continue
        print(f"  {code}: {airport.name} ({airport.iso_country}) — {len(airport.runways)} runways")


def show_flight_statuses(sky: SkyLink) -> None:
    """Live status of several flights at once."""

    statuses = sky.batch.flight_statuses(FLIGHTS)

    print("\nFlights")
    for number, status in statuses.items():
        if isinstance(status, SkyLinkError):
            print(f"  {number}: no status ({type(status).__name__})")
            continue
        # Scraped payload: every field is an optional string, times are opaque.
        arrival = status.arrival.estimated_time if status.arrival else None
        print(f"  {number}: {status.status or '?'} (arriving {arrival or '--'})")


def main() -> None:
    try:
        with SkyLink() as sky:  # RapidAPI channel, api_key falls back to $RAPIDAPI_KEY
            show_metars(sky)
            show_notams(sky)
            show_airports(sky)
            show_flight_statuses(sky)
    except AuthenticationError as err:
        raise SystemExit(f"Auth failed: {err}") from err
    except RateLimitError as err:
        # A batch multiplies quota use: concurrency defaults to 5 for that reason.
        raise SystemExit(f"Quota exhausted, retry in {err.retry_after}s") from err
    except APIStatusError as err:
        raise SystemExit(f"API error {err.status_code}: {err.message}") from err


if __name__ == "__main__":
    main()
