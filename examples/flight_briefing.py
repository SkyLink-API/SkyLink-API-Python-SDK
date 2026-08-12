"""Pre-flight briefing in three shapes: structured JSON, markdown, and a PDF.

``briefing.flight()`` is overloaded on ``format``: ``format="json"`` (the
default) returns a :class:`FlightBriefing` model, the three text formats return
the rendered document as a plain ``str``. ``briefing.pdf()`` is the API's only
binary endpoint and hands back ``bytes``.

Run it with a key in the environment::

    export RAPIDAPI_KEY=...msh...jsn...   # or SKYLINK_API_KEY with provider="direct"
    python examples/flight_briefing.py
"""

from __future__ import annotations

from pathlib import Path

from skylink_api import (
    APIStatusError,
    AuthenticationError,
    FlightBriefing,
    RateLimitError,
    SkyLink,
)

ORIGIN, DESTINATION = "KJFK", "EGLL"
OUTPUT_DIR = Path("briefings")


def show_structured(sky: SkyLink) -> FlightBriefing:
    """format="json" → a typed model."""

    briefing = sky.briefing.flight(
        origin=ORIGIN,
        destination=DESTINATION,
        include_weather=True,
        include_notams=True,
        include_pireps=False,  # slowest source, off by default
    )

    print(f"{briefing.origin} → {briefing.destination}")
    print(f"  {briefing.summary}")
    print(f"  data included: {', '.join(briefing.data_included)}")

    for restriction in briefing.critical_restrictions:
        print(f"  ! {restriction.icao}: {restriction.description}")

    origin_briefing = briefing.origin_briefing
    if origin_briefing is not None and origin_briefing.weather is not None:
        print(f"  {ORIGIN} METAR: {origin_briefing.weather.metar_raw}")
    if origin_briefing is not None and origin_briefing.notams is not None:
        # None (not []) whenever include_notams was False.
        print(f"  {ORIGIN} NOTAMs: {len(origin_briefing.notams)}")

    return briefing


def show_markdown(sky: SkyLink) -> str:
    """A text format → the document itself, already unwrapped from its envelope."""

    document = sky.briefing.flight(
        origin=ORIGIN,
        destination=DESTINATION,
        format="markdown",
    )
    # Static type here is `str`, not FlightBriefing — the overload follows the
    # value of `format`.
    head = document.splitlines()[:8]
    print("\nMarkdown briefing (first lines):")
    for line in head:
        print(f"  {line}")
    return document


def save_pdf(sky: SkyLink) -> Path:
    """The one endpoint that is not JSON: raw PDF bytes."""

    pdf: bytes = sky.briefing.pdf(
        departure_icao=ORIGIN,
        arrival_icao=DESTINATION,
        flight_number="BA112",
    )

    OUTPUT_DIR.mkdir(exist_ok=True)
    target = OUTPUT_DIR / f"{ORIGIN}-{DESTINATION}.pdf"
    target.write_bytes(pdf)
    print(f"\nSaved {len(pdf)} bytes to {target} (starts with {pdf[:5]!r})")
    return target


def main() -> None:
    try:
        with SkyLink() as sky:  # RapidAPI channel, api_key falls back to $RAPIDAPI_KEY
            show_structured(sky)
            show_markdown(sky)
            save_pdf(sky)
    except AuthenticationError as err:
        raise SystemExit(f"Auth failed: {err}") from err
    except RateLimitError as err:
        raise SystemExit(f"Quota exhausted, retry in {err.retry_after}s") from err
    except APIStatusError as err:
        # 400 when every include_* flag is False, 404 for an unknown ICAO code,
        # 503 when the briefing model or one of its sources is unavailable.
        raise SystemExit(f"API error {err.status_code}: {err.message}") from err


if __name__ == "__main__":
    main()
