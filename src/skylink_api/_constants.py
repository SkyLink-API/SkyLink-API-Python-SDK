"""Static values shared by every SkyLink client.

Two delivery channels are supported:

* ``direct``   — ``https://data.skylinkapi.com/v3.1`` with an ``x-api-key`` header.
* ``rapidapi`` — ``https://skylink-api.p.rapidapi.com`` (**no** version prefix — the
  RapidAPI listing is pinned to v3.1) with ``X-RapidAPI-Key``/``X-RapidAPI-Host``.

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
DEFAULT_MAX_RETRIES: Final = 3
DEFAULT_PROVIDER: Final[Literal["direct"]] = "direct"
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

# ── Response headers ─────────────────────────────────────────────────────────

RATE_LIMIT_LIMIT_HEADER: Final = "X-RateLimit-Requests-Limit"
RATE_LIMIT_REMAINING_HEADER: Final = "X-RateLimit-Requests-Remaining"
RATE_LIMIT_RESET_HEADER: Final = "X-RateLimit-Requests-Reset"
RETRY_AFTER_HEADER: Final = "Retry-After"
