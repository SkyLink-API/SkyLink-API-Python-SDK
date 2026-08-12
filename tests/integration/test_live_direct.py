"""The direct channel (``data.skylinkapi.com``) is addressed correctly.

The SDK ships two delivery channels and both have to work: the RapidAPI
marketplace (``skylink-api.p.rapidapi.com``, no version prefix, ``X-RapidAPI-*``
headers) and the direct gateway (``data.skylinkapi.com/v3.1``, ``x-api-key``).
The rest of the live suite runs against whichever channel it is armed for
(``SKYLINK_TEST_PROVIDER``), but a direct key is a separate Polar licence key
that CI does not hold — and a marketplace key is rejected on
``data.skylinkapi.com``, so without this file an unattended run would prove
nothing at all about the direct channel.

Rejection is exactly what makes the channel testable without a key. The gateway
authenticates *before* routing, and it answers with its own error envelope::

    GET /v3.1/weather/metar/KJFK, x-api-key: <not a direct key>
        → 401 {"code": "invalid_api_key",  "message": "Invalid API key"}
    GET /v3.1/weather/metar/KJFK, no key header at all
        → 401 {"code": "missing_api_key",  "message": "API key is required"}

The two codes are what makes this useful rather than tautological. The gateway
answers ``missing_api_key`` unless the credential arrives in ``x-api-key``
specifically — ``authorization: Bearer ...`` and ``x-rapidapi-key`` both count as
"no key" (pinned by
:func:`test_direct_live_key_must_travel_in_the_x_api_key_header`). So a live
``invalid_api_key`` proves:

1. the host the SDK builds is real, routable and speaking HTTPS — not a DNS
   failure (:class:`~skylink_api.APIConnectionError`) or a timeout;
2. the credential travelled in the header the gateway actually reads;
3. the SDK parses the gateway envelope (``{code, message}``, served as
   ``text/plain``), not just the application's ``{"detail": ...}``;
4. it maps ``401`` onto :class:`~skylink_api.AuthenticationError` with the code
   preserved on ``.code``.

What a live 401 deliberately does *not* prove is the **path**: the gateway
authenticates before routing, so ``/v9.9/nope/nope`` is rejected exactly like
``/v3.1/weather/metar/KJFK`` (pinned by
:func:`test_direct_live_gateway_authenticates_before_routing`, so nobody reads
more into a 401 than it says). URL construction is asserted offline in the first
section here and throughout the unit suite, which runs against the direct base
URL precisely because its ``/v3.1`` prefix catches a lost or doubled version
segment. Nor does any of this prove that a valid key returns data — that is
:mod:`tests.integration.test_live`'s job, on whichever channel it is armed for.

What a run *with* a direct key adds, and this file cannot: the quota headers
(the direct gateway spells them ``X-RateLimit-*``, RapidAPI
``X-RateLimit-Requests-*``) and the plan-gated writes in
:mod:`tests.integration.test_live_webhooks`.

Gating: these tests need no ``SKYLINK_TEST_API_KEY`` (the keys here are invalid
on purpose), only a network, so they skip offline via
:func:`tests.integration.conftest.require_network` instead of failing. Like the
rest of the directory they are collected only when it is asked for explicitly::

    ./.venv/Scripts/python.exe -m pytest tests/integration/test_live_direct.py -rs -v
"""

from __future__ import annotations

import httpx
import pytest
import respx

from skylink_api import (
    AsyncSkyLink,
    AuthenticationError,
    SkyLink,
)
from skylink_api._constants import (
    DIRECT_BASE_URL,
    RAPIDAPI_BASE_URL,
    RAPIDAPI_HOST,
)

from .conftest import require_network

pytestmark = pytest.mark.integration

#: Syntactically plausible, definitely not issued. Any key the gateway does not
#: know — including a valid RapidAPI subscription key — gets the same answer.
INVALID_KEY = "sk_live_not_a_real_direct_key_0000000000"

#: Cheap, always-routed endpoint: auth runs before the handler, so the upstream
#: weather source is never touched and no quota is spent.
PROBE_ICAO = "KJFK"


def _direct_client(*, api_key: str | None = None, base_url: str | None = None) -> SkyLink:
    """Blocking direct-channel client.

    Retries are off — a ``401`` is never retried, and turning them off keeps a
    genuine outage from stretching the test over four backoff sleeps. ``environ``
    is emptied so an ambient ``SKYLINK_API_KEY`` cannot make the run pass or fail
    for the wrong reason.
    """

    return SkyLink(
        provider="direct",
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
        timeout=20.0,
        environ={},
    )


def _async_direct_client(
    *, api_key: str | None = None, base_url: str | None = None
) -> AsyncSkyLink:
    """Asyncio counterpart of :func:`_direct_client`."""

    return AsyncSkyLink(
        provider="direct",
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
        timeout=20.0,
        environ={},
    )


def _assert_gateway_401(error: AuthenticationError, *, code: str) -> None:
    """The gateway's 401 envelope, as the SDK is expected to surface it."""

    assert error.status_code == 401
    assert error.code == code, f"expected code {code!r}, got {error.code!r} (body {error.body!r})"
    # Shape A from ``skylink_api._exceptions``: ``{code, message}``, no ``detail``.
    # The gateway labels it ``text/plain``, so a content-type sniffing parser would
    # leave ``body`` a raw string here and lose ``code`` with it.
    assert isinstance(error.body, dict), f"error body was not parsed as JSON: {error.body!r}"
    assert error.body.get("code") == code
    # ``message`` came from the envelope, not from the ``HTTP <status>`` fallback.
    assert error.message and error.message != f"HTTP {error.status_code}"
    assert error.errors == ()  # not a 422 validation payload


# ── 1. addressing (offline: documents what each channel targets) ─────────────


def test_direct_provider_targets_the_gateway(respx_mock: respx.MockRouter) -> None:
    """``provider="direct"`` → ``data.skylinkapi.com/v3.1`` + ``x-api-key``."""

    route = respx_mock.route().mock(return_value=httpx.Response(200, json={"ok": True}))
    with SkyLink(provider="direct", api_key="sk_direct", environ={}) as sky:
        assert sky.base_url == DIRECT_BASE_URL == "https://data.skylinkapi.com/v3.1"
        sky.request("GET", f"/weather/metar/{PROBE_ICAO}")

    request = route.calls.last.request
    assert str(request.url) == f"https://data.skylinkapi.com/v3.1/weather/metar/{PROBE_ICAO}"
    assert request.headers["x-api-key"] == "sk_direct"
    assert "x-rapidapi-key" not in request.headers
    assert "x-rapidapi-host" not in request.headers


def test_rapidapi_provider_targets_the_marketplace(respx_mock: respx.MockRouter) -> None:
    """``provider="rapidapi"`` (the default) → the marketplace host, no version prefix."""

    route = respx_mock.route().mock(return_value=httpx.Response(200, json={"ok": True}))
    with SkyLink(provider="rapidapi", api_key="rapid_key", environ={}) as sky:
        assert sky.base_url == RAPIDAPI_BASE_URL
        sky.request("GET", f"/weather/metar/{PROBE_ICAO}")

    request = route.calls.last.request
    assert str(request.url) == f"https://skylink-api.p.rapidapi.com/weather/metar/{PROBE_ICAO}"
    assert request.headers["x-rapidapi-key"] == "rapid_key"
    assert request.headers["x-rapidapi-host"] == RAPIDAPI_HOST
    assert "x-api-key" not in request.headers
    assert "data.skylinkapi.com" not in str(request.url)


def test_direct_namespace_call_keeps_the_version_prefix(respx_mock: respx.MockRouter) -> None:
    """A typed namespace call lands on ``/v3.1/...``, not on ``/...`` or ``/v3.1/v3.1/...``."""

    route = respx_mock.get(url__startswith=f"{DIRECT_BASE_URL}/weather/metar").mock(
        return_value=httpx.Response(200, json={"icao": PROBE_ICAO, "raw": "KJFK 010000Z ..."})
    )
    with SkyLink(provider="direct", api_key="sk_direct", environ={}) as sky:
        sky.weather.metar(PROBE_ICAO)

    assert route.calls.last.request.url.path == f"/v3.1/weather/metar/{PROBE_ICAO}"


# ── 2. live: the gateway rejects an unknown key ──────────────────────────────


def test_direct_live_invalid_key_raises_authentication_error() -> None:
    """A wrong key reaches the *gateway* and comes back as ``invalid_api_key``.

    The value of the assertion is what it rules out: DNS failure, a 404 from a
    wrong prefix, a timeout, or an unparsed error body.
    """

    require_network(DIRECT_BASE_URL)

    with _direct_client(api_key=INVALID_KEY) as sky:
        assert sky.base_url == DIRECT_BASE_URL
        with pytest.raises(AuthenticationError) as excinfo:
            sky.weather.metar(PROBE_ICAO)

    _assert_gateway_401(excinfo.value, code="invalid_api_key")


def test_direct_live_missing_key_raises_authentication_error() -> None:
    """No key header at all → ``missing_api_key``, same 401 mapping.

    A key is optional only because ``base_url`` is explicit (that is the escape
    hatch for staging instances running ``DISABLE_AUTH=true``); production keeps
    authenticating, and the SDK surfaces the refusal as an
    :class:`~skylink_api.AuthenticationError` rather than swallowing it.
    """

    require_network(DIRECT_BASE_URL)

    with _direct_client(base_url=DIRECT_BASE_URL) as sky:
        assert sky.api_key is None
        with pytest.raises(AuthenticationError) as excinfo:
            sky.weather.metar(PROBE_ICAO)

    _assert_gateway_401(excinfo.value, code="missing_api_key")


def test_direct_live_error_is_the_gateway_envelope() -> None:
    """The 401 is the gateway's, not the application's.

    Guards against reporting "authentication error" for something else entirely:
    an app-level 401 would carry ``{"detail": ...}`` and no ``code``, which is a
    different failure with a different fix.
    """

    require_network(DIRECT_BASE_URL)

    with (
        _direct_client(api_key=INVALID_KEY) as sky,
        pytest.raises(AuthenticationError) as excinfo,
    ):
        sky.weather.metar(PROBE_ICAO)

    body = excinfo.value.body
    assert isinstance(body, dict), f"expected a JSON envelope, got {body!r}"
    assert "detail" not in body
    assert set(body) >= {"code", "message"}


def test_direct_live_key_must_travel_in_the_x_api_key_header() -> None:
    """The control experiment: a key in the wrong header is *no* key.

    Same request, same credential, only the header name changed — and the gateway
    switches to ``missing_api_key``. That is what turns
    :func:`test_direct_live_invalid_key_raises_authentication_error` into evidence
    about the SDK: had it sent the key under ``authorization`` or under the
    RapidAPI headers, that test would have seen ``missing_api_key`` too.
    """

    require_network(DIRECT_BASE_URL)

    for header in ("authorization", "x-rapidapi-key"):
        value = f"Bearer {INVALID_KEY}" if header == "authorization" else INVALID_KEY
        with (
            SkyLink(
                provider="direct",
                base_url=DIRECT_BASE_URL,
                default_headers={header: value},
                max_retries=0,
                timeout=20.0,
                environ={},
            ) as sky,
            pytest.raises(AuthenticationError) as excinfo,
        ):
            sky.weather.metar(PROBE_ICAO)

        assert excinfo.value.code == "missing_api_key", (
            f"the gateway read {header!r} as a credential; "
            "the discriminator this file relies on no longer holds"
        )


def test_direct_live_gateway_authenticates_before_routing() -> None:
    """A 401 says nothing about the path — pin that, so no test claims otherwise.

    An unrouted, invented path is rejected with the very same envelope as the real
    one, because authentication happens in front of the application. Path building
    is therefore an offline assertion (see the first section); this test exists to
    keep the live ones honest about their own scope.
    """

    require_network(DIRECT_BASE_URL)

    with (
        _direct_client(api_key=INVALID_KEY) as sky,
        pytest.raises(AuthenticationError) as excinfo,
    ):
        sky.request("GET", "/there-is-no-such-endpoint")

    _assert_gateway_401(excinfo.value, code="invalid_api_key")


# ── 3. live: the async client behaves identically ────────────────────────────


async def test_direct_live_invalid_key_async() -> None:
    """Same channel, same rejection, over :class:`AsyncSkyLink`."""

    require_network(DIRECT_BASE_URL)

    async with _async_direct_client(api_key=INVALID_KEY) as sky:
        assert sky.base_url == DIRECT_BASE_URL
        with pytest.raises(AuthenticationError) as excinfo:
            await sky.weather.metar(PROBE_ICAO)

    _assert_gateway_401(excinfo.value, code="invalid_api_key")


async def test_direct_live_missing_key_async() -> None:
    """Keyless async request → ``missing_api_key``."""

    require_network(DIRECT_BASE_URL)

    async with _async_direct_client(base_url=DIRECT_BASE_URL) as sky:
        assert sky.api_key is None
        with pytest.raises(AuthenticationError) as excinfo:
            await sky.weather.metar(PROBE_ICAO)

    _assert_gateway_401(excinfo.value, code="missing_api_key")
