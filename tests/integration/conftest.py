"""Gate, clients and tolerance helpers for the live integration suite.

The suite only runs against a real deployment, so it is doubly gated:

* **Collection.** ``pytest_ignore_collect`` skips this directory entirely unless
  it was asked for explicitly — by path (``pytest tests/integration``) or by
  marker (``pytest -m integration``). A plain ``pytest`` therefore keeps
  collecting exactly the offline unit suite, mirroring what ``npm test`` does in
  the TypeScript SDK.
* **Execution.** Once collected, every test carries a module level
  ``skipif`` on :data:`BASE_URL`, so an explicit run without the environment
  variable reports clean skips rather than connection failures.

Usage::

    # staging container from the backend's docker-compose.test.yml
    SKYLINK_TEST_BASE_URL=http://localhost:8081/v3.1 \
        ./.venv/Scripts/python.exe -m pytest tests/integration -v

    # production, direct channel, with a key
    SKYLINK_TEST_BASE_URL=https://data.skylinkapi.com/v3.1 \
    SKYLINK_TEST_API_KEY=sk_live_... \
        ./.venv/Scripts/python.exe -m pytest tests/integration -v

    # production, RapidAPI channel (base URL comes from the provider)
    SKYLINK_TEST_PROVIDER=rapidapi \
    SKYLINK_TEST_API_KEY=...msh...jsn... \
        ./.venv/Scripts/python.exe -m pytest tests/integration -v

``SKYLINK_TEST_API_KEY`` is optional: with an explicit ``base_url`` the SDK does
not require a key, which is what staging instances running ``DISABLE_AUTH=true``
expect.

``SKYLINK_TEST_PROVIDER`` selects the channel (``direct`` by default). Setting it
to ``rapidapi`` sends ``X-RapidAPI-Key``/``X-RapidAPI-Host`` and, unless
``SKYLINK_TEST_BASE_URL`` overrides it, targets the marketplace host — which is
why either variable on its own is enough to arm the suite.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from skylink_api import (
    APIStatusError,
    AsyncSkyLink,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    Provider,
    ServiceUnavailableError,
    SkyLink,
)

# ── gate ─────────────────────────────────────────────────────────────────────

BASE_URL_ENV = "SKYLINK_TEST_BASE_URL"
API_KEY_ENV = "SKYLINK_TEST_API_KEY"
PROVIDER_ENV = "SKYLINK_TEST_PROVIDER"

#: Endpoint under test, e.g. ``http://localhost:8081/v3.1``. Empty → provider default.
BASE_URL = os.environ.get(BASE_URL_ENV, "").strip()

#: Optional key. Empty → the client is built with ``base_url`` only.
API_KEY = os.environ.get(API_KEY_ENV, "").strip()

#: Channel to exercise: ``direct`` (default) or ``rapidapi``.
PROVIDER: Provider = (
    "rapidapi" if os.environ.get(PROVIDER_ENV, "").strip() == "rapidapi" else "direct"
)

#: The suite is armed by a base URL (any channel) or by naming a provider with a key.
ARMED = bool(BASE_URL) or (PROVIDER == "rapidapi" and bool(API_KEY))

SKIP_REASON = (
    f"live API tests need {BASE_URL_ENV} "
    f"(e.g. {BASE_URL_ENV}=http://localhost:8081/v3.1) "
    f"or {PROVIDER_ENV}=rapidapi with {API_KEY_ENV}"
)

_INTEGRATION_DIR = Path(__file__).parent.resolve()


def _explicitly_selected(config: pytest.Config) -> bool:
    """Was this directory named on the command line, by path or by marker?"""

    markexpr = str(getattr(config.option, "markexpr", "") or "")
    if "integration" in markexpr:
        return True

    invocation_dir = Path(config.invocation_params.dir)
    for arg in config.args:
        # Strip the ``::node::id`` part; what is left is a filesystem path.
        raw = str(arg).split("::", 1)[0]
        if not raw:
            continue
        try:
            target = (invocation_dir / raw).resolve()
        except OSError:  # pragma: no cover - exotic paths only
            continue
        if target.is_relative_to(_INTEGRATION_DIR):
            return True
    return False


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Keep live tests out of ordinary runs.

    Returning ``True`` drops the path from collection; ``None`` means "no
    opinion" and lets the default collection proceed.
    """

    if _explicitly_selected(config):
        return None
    return True


# ── clients ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def sky() -> Iterator[SkyLink]:
    """One blocking client for the whole session (one pooled connection)."""

    with SkyLink(
        provider=PROVIDER,
        base_url=BASE_URL or None,
        api_key=API_KEY or None,
        environ={},
    ) as client:
        yield client


@pytest.fixture
async def async_sky() -> AsyncIterator[AsyncSkyLink]:
    """Asyncio counterpart, function scoped to match pytest-asyncio's event loop."""

    async with AsyncSkyLink(
        provider=PROVIDER,
        base_url=BASE_URL or None,
        api_key=API_KEY or None,
        environ={},
    ) as client:
        yield client


# ── tolerance helpers ────────────────────────────────────────────────────────

#: Outcomes that are *correct behaviour*, not SDK bugs, on a live deployment.
#:
#: Endpoints backed by scraped or streamed third-party data (weather, NOTAMs,
#: FAA delays, charts, tickets, briefings, routes) answer ``503`` while the
#: upstream is down or the in-process cache is still warming, and ``404`` when
#: the identifier simply has no live data right now. History answers ``503``
#: without a TimescaleDB behind it.
UPSTREAM_ERRORS: tuple[type[APIStatusError], ...] = (NotFoundError, ServiceUnavailableError)

#: Outcomes for the plan gated webhook endpoints.
#:
#: A key without the webhook entitlement gets ``403`` (and ``401`` when the
#: staging instance authenticates but does not know the key at all); ``503``
#: means the subscription store is not up. None of the three is an SDK bug.
PLAN_ERRORS: tuple[type[APIStatusError], ...] = (
    AuthenticationError,
    PermissionDeniedError,
    ServiceUnavailableError,
)


def describe(error: APIStatusError) -> str:
    """Compact one-liner for a skip reason."""

    body = error.body
    detail = body.get("detail") if isinstance(body, dict) else body
    return f"HTTP {error.status_code}: {detail if detail else error.message}"


@contextmanager
def tolerating(
    what: str,
    *,
    errors: tuple[type[APIStatusError], ...] = UPSTREAM_ERRORS,
) -> Iterator[None]:
    """Run a live call, turning expected upstream failures into a skip.

    Wrap the call *and* its assertions::

        with tolerating("GET /notams/{icao}"):
            result = sky.notams.by_airport("KJFK")
            assert isinstance(result, NotamsResponse)

    Only ``errors`` are swallowed — an ``AssertionError``, a parsing failure or
    any other status code still fails the test, which is the whole point of the
    suite.
    """

    try:
        yield
    except errors as exc:
        pytest.skip(f"{what}: upstream data unavailable, {describe(exc)}")
