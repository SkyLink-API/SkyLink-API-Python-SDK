# Fixture sources

Test fixtures for the SkyLink SDKs (task C1). Identical set in both repos:
`Python-SDK-/tests/fixtures/` and `TypeScript-SDK/tests/fixtures/`.

Paths are relative to `SkyLink-API-V3.1/` unless noted otherwise.
Line numbers refer to the repository state of 2026-08-12.

## Verbatim from OpenAPI `example` / `examples` blocks

| Fixture | Source |
|---|---|
| `tickets_search.json` | `routers/v31/tickets.py:34-110` |
| `flight_status.json` | `routers/v3/flight_status.py:22-48` |
| `schedules_departures.json` | `routers/v3/schedules.py:136-150` (PascalCase keys in `flights[]`) |
| `airports_search.json` | `routers/airports.py:16-58` |
| `notams.json` | `routers/v31/notams.py:53-75` |
| `briefing_flight.json` | `routers/v3/flight_briefing.py:59-96` (`examples["json"].value`) |
| `briefing_text.json` | `routers/v3/flight_briefing.py:97-107` (`examples["markdown"].value`; the `plain_text` and `html` variants at `:108-129` share this shape) |
| `charts.json` | `routers/v3/charts.py:59-68` (partial category map; `url` is the literal `"https://..."` placeholder from the example) |
| `weather_metar.json` | `routers/weather.py:232-237` (unparsed form; `?parsed=true` adds a `parsed` key) |
| `performance.json` | `routers/v31/performance.py:31-44` |

## Hand-built (no example block in the router)

| Fixture | Source of the shape |
|---|---|
| `adsb_aircraft.json` | hand-built from research/02 §ADS-B (`models/adsb_models.py`) — `AircraftListResponse` envelope; second element exercises the all-null no-position aircraft; `timestamp`/`last_seen`/`first_seen` are naive ISO without `Z` (Pydantic `datetime.now` default) |
| `aircraft_found.json` | hand-built from research/02 §Aircraft lookup (`models/v31/aircraft.py`) — note `year_built` is a **string** |
| `aircraft_not_found.json` | hand-built from research/02 §Aircraft lookup — `found:false ⇒ aircraft:null` on HTTP 200 |
| `history_flights.json` | hand-built from research/02 §History; 27-column row from `services/v31/history_service.py:117-147`, envelope from `routers/v31/history_ultra.py:137-150`; second flight is the sparse in-progress form; `icao24` UPPERCASE in rows, lowercase in `filters` |
| `history_empty_note.json` | verbatim envelope from `routers/v31/history_ultra.py:111-123` (registration not in the aircraft DB → `count:0`, `flights:[]`, `note`) |
| `history_track.json` | hand-built from research/02 §History; envelope `routers/v31/history_ultra.py:219-234`, 13-column position row `services/v31/history_service.py:34-59` (note `altitude_baro`, not `altitude`) |
| `webhook_created.json` | hand-built from research/02 §Webhooks / `services/v31/webhook_service.py:140-147` — POST 201 body (6 keys) |
| `webhooks_list.json` | hand-built from `routers/v31/webhooks.py:146` + `services/v31/webhook_service.py:164-174` — list rows are 2 keys wider than the create body (`last_triggered_at`, `failure_count`) |
| `routes_vrs.json` | hand-built from research/01 §Routes / `services/v31/route_service.py:211-220` — `source:"vrs"` branch |
| `routes_airline.json` | hand-built from research/01 §Routes / `services/v31/route_service.py:236-244` — `source:"airline_routes"` branch, **no `callsign` key** |
| `carbon.json` | hand-built from research/02 §Carbon / `services/v31/carbon_service.py:195-226` — `include_rfi=false` ⇒ `co2_equivalent_*` null; `callsign` supplied ⇒ the three conditional keys are present |
| `errors_401.json` | verbatim from `main.py:190-201` (`_json_401`), research/01 §5 form A |
| `errors_404.json` | hand-built from research/01 §5 form B — `HTTPException` detail string |
| `errors_422_validation.json` | hand-built from research/01 §5 form C — FastAPI request-validation payload |

## Notes for SDK tests

- Nulls and empty collections are preserved exactly as the backend emits them
  (`layovers: null`, `pireps: null`, `notams: []`, `checkin: ""`).
- Six datetime encodings appear across the set (research/02 §Datetime-политика):
  naive ISO (adsb, tickets), ISO with `Z` (weather, charts), `+00:00` (history,
  webhooks), `YYMMDDHHMM` (notams), and year-less local strings
  (flight_status, schedules). None of the string-typed ones should be parsed as
  datetimes by the SDK.
- `charts.json.charts` is a **partial** category map — empty categories are
  dropped server-side.
