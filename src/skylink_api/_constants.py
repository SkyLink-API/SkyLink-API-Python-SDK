"""Static values shared by every SkyLink client.

Two delivery channels are supported:

* ``rapidapi`` (**default**) — ``https://skylink-api.p.rapidapi.com`` (**no** version
  prefix — the RapidAPI listing is pinned to v3.1) with
  ``X-RapidAPI-Key``/``X-RapidAPI-Host``.
* ``direct`` — ``https://data.skylinkapi.com/v3.1`` with an ``x-api-key`` header.

RapidAPI is the default because that is how most subscriptions reach the API; the
direct channel is one ``provider="direct"`` away.

Resource paths themselves are version independent; the version lives in the direct
channel's base URL only.
"""

from __future__ import annotations

from typing import Final, Literal

import httpx

from ._version import __version__

# ── Endpoints ────────────────────────────────────────────────────────────────

DIRECT_BASE_URL: Final = "https://data.skylinkapi.com/v3.1"
RAPIDAPI_BASE_URL: Final = "https://skylink-api.p.rapidapi.com"
RAPIDAPI_HOST: Final = "skylink-api.p.rapidapi.com"

# ── Credentials ──────────────────────────────────────────────────────────────

DIRECT_API_KEY_ENV: Final = "SKYLINK_API_KEY"
RAPIDAPI_KEY_ENV: Final = "RAPIDAPI_KEY"

DIRECT_API_KEY_HEADER: Final = "x-api-key"
RAPIDAPI_KEY_HEADER: Final = "X-RapidAPI-Key"
RAPIDAPI_HOST_HEADER: Final = "X-RapidAPI-Host"

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT: Final = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)

#: Read timeout for ``/briefing/*``, the one family the 30 s default is wrong for.
#:
#: The briefings are composed by a language model over METAR, TAF, NOTAMs and
#: (optionally) PIREPs for both airports. Measured against the live API on
#: 2026-08-15: ``format="json"`` took 38-58 s, ``markdown`` 30-50 s and
#: ``plain_text`` up to 85 s. Under :data:`DEFAULT_TIMEOUT` every one of those is
#: an :class:`~skylink_api.APITimeoutError` — and because a timeout is retried,
#: the caller waits ~120 s to be told a working endpoint failed.
#:
#: Applied as the *spec* default (:attr:`skylink_api._types.RequestSpec.timeout`),
#: so ``request_options={"timeout": ...}`` still overrides it.
BRIEFING_TIMEOUT: Final = httpx.Timeout(connect=5.0, read=180.0, write=30.0, pool=5.0)
DEFAULT_MAX_RETRIES: Final = 3
DEFAULT_PROVIDER: Final[Literal["rapidapi"]] = "rapidapi"
DEFAULT_HISTORY_PLAN: Final[Literal["ultra"]] = "ultra"

USER_AGENT: Final = f"skylink-api-python/{__version__}"

# ── Retry policy ─────────────────────────────────────────────────────────────

#: Statuses worth retrying: throttling plus transient upstream failures.
RETRYABLE_STATUS_CODES: Final = frozenset({429, 500, 502, 503, 504})

#: Methods that must not be replayed after a transport error or a 5xx — replaying
#: ``POST /webhooks`` would create duplicate subscriptions. 429 is still retried
#: for these because the request provably never reached the handler.
UNSAFE_METHODS: Final = frozenset({"POST"})

#: Full-jitter backoff: ``random() * min(MAX_RETRY_DELAY, INITIAL * 2 ** attempt)``.
INITIAL_RETRY_DELAY: Final = 0.5
MAX_RETRY_DELAY: Final = 8.0

#: Upper bound for a server supplied ``Retry-After`` (seconds or HTTP-date).
MAX_RETRY_AFTER: Final = 60.0

# ── Enumerable values ────────────────────────────────────────────────────────
#
# The runtime twins of the ``Literal`` types declared on the resources. The
# literals are for type checkers; these tuples are for the things you can only do
# at runtime — populating a dropdown, validating user input, looping over every
# category. Each one is kept in the order the backend declares it, and each is
# asserted against its literal in ``tests/test_constants.py``.

#: Aerodrome chart categories — ``models/v3/charts.py:ChartCategory`` on the
#: backend. ``GEN`` general, ``GND`` ground/taxi, ``SID`` departure, ``STAR``
#: arrival, ``APP`` approach. Runtime twin of
#: :data:`~skylink_api.resources.charts.ChartCategory`.
CHART_CATEGORIES: Final[tuple[str, ...]] = ("GEN", "GND", "SID", "STAR", "APP")

#: Webhook event names — ``services/v31/webhook_service.py:VALID_EVENTS``.
#: Runtime twin of :data:`~skylink_api.resources.webhooks.WebhookEventType` and
#: of the :class:`~skylink_api.models.webhooks.WebhookEvent` enum.
#: ``GET /webhooks/events`` returns the same six names, sorted.
WEBHOOK_EVENTS: Final[tuple[str, ...]] = (
    "status_changed",
    "flight_delayed",
    "flight_cancelled",
    "flight_boarding",
    "flight_landed",
    "gate_changed",
)

#: Continent codes accepted by ``geo.countries()`` and ``geo.regions()`` —
#: ``_VALID_CONTINENTS`` in the backend's ``routers/v3/countries.py``.
#:
#: .. versionchanged:: 0.2.0
#:    ``"NA"`` (North America) used to be accepted but resolve to nothing: the
#:    backend read its reference CSV with pandas, which parses the literal ``NA``
#:    as *not-a-number*, so every North American country arrived with an empty
#:    ``continent`` and the filter matched zero rows. The backend now loads the
#:    CSV without that coercion; verified live on 2026-08-15,
#:    ``geo.countries(continent="NA")`` returns 41 countries and
#:    ``geo.regions(continent="NA")`` 440 regions.
CONTINENTS: Final[tuple[str, ...]] = ("AF", "AN", "AS", "EU", "NA", "OC", "SA")

#: History plan prefixes — the ``/{plan}/history/*`` URL segment. ``"ultra"``
#: allows a **90**-day window, ``"mega"`` 365 days (``_MAX_DAYS`` in the
#: backend's ``routers/v31/history_{ultra,mega}.py``); a ``[start, end]`` range
#: longer than that is a **422** from the route itself. A ``403`` is the
#: different failure of calling a plan the key is not subscribed to, which the
#: gateway rejects before the route is reached. Runtime twin of
#: :data:`~skylink_api.HistoryPlan`.
HISTORY_PLANS: Final[tuple[str, ...]] = ("ultra", "mega")

#: US flight categories, best to worst. **Not an API value** — the backend does
#: not classify reports, :func:`skylink_api.helpers.weather.flight_category`
#: derives them from visibility and ceiling. Runtime twin of
#: :data:`~skylink_api.helpers.weather.FlightCategory`.
FLIGHT_CATEGORIES: Final[tuple[str, ...]] = ("VFR", "MVFR", "IFR", "LIFR")

# ── Response headers ─────────────────────────────────────────────────────────
#
# The two channels spell the same quota differently, so the SDK understands both.
#
# * **RapidAPI** sends ``X-RateLimit-Requests-{Limit,Remaining,Reset}`` — the
#   request quota of the subscribed plan.
# * **Direct** (the APISIX gateway in front of ``data.skylinkapi.com``) sends the
#   unprefixed ``X-RateLimit-{Limit,Remaining,Reset}`` — and spells them
#   ``X-Ratelimit-Limit`` on the wire, which is fine: header names are
#   case-insensitive and :func:`~skylink_api._response.get_header` treats them so.

#: RapidAPI's per-plan request quota.
RATE_LIMIT_LIMIT_HEADER: Final = "X-RateLimit-Requests-Limit"
RATE_LIMIT_REMAINING_HEADER: Final = "X-RateLimit-Requests-Remaining"
RATE_LIMIT_RESET_HEADER: Final = "X-RateLimit-Requests-Reset"

#: The direct gateway's quota, same meaning without the ``Requests`` infix.
DIRECT_RATE_LIMIT_LIMIT_HEADER: Final = "X-RateLimit-Limit"
DIRECT_RATE_LIMIT_REMAINING_HEADER: Final = "X-RateLimit-Remaining"
DIRECT_RATE_LIMIT_RESET_HEADER: Final = "X-RateLimit-Reset"

#: ``(limit, remaining, reset)`` header names, **in priority order**. The first
#: family that yields a number wins.
#:
#: RapidAPI comes first on purpose: it answers with *both* families plus a third,
#: noisy one — ``X-RateLimit-rapid-free-plans-hard-limit-{limit,remaining,reset}``,
#: the marketplace's global free-tier ceiling rather than this key's quota. Only
#: exact (case-insensitive) name matches are accepted, so that triplet is never
#: mistaken for either family, and the generic names never shadow the plan quota.
RATE_LIMIT_HEADER_SETS: Final[tuple[tuple[str, str, str], ...]] = (
    (RATE_LIMIT_LIMIT_HEADER, RATE_LIMIT_REMAINING_HEADER, RATE_LIMIT_RESET_HEADER),
    (
        DIRECT_RATE_LIMIT_LIMIT_HEADER,
        DIRECT_RATE_LIMIT_REMAINING_HEADER,
        DIRECT_RATE_LIMIT_RESET_HEADER,
    ),
)

RETRY_AFTER_HEADER: Final = "Retry-After"
