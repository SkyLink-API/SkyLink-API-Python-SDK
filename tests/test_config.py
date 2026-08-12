"""Configuration resolution: providers, env fallback, base URL, headers."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._config import resolve_config
from skylink_api._constants import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    DIRECT_BASE_URL,
    RAPIDAPI_BASE_URL,
    RAPIDAPI_HOST,
    USER_AGENT,
)
from skylink_api._exceptions import AuthenticationError
from skylink_api._version import __version__

STAGING_URL = "http://localhost:8081/v3.1"


def _ok(respx_mock: respx.MockRouter) -> respx.Route:
    return respx_mock.route().mock(return_value=httpx.Response(200, json={"ok": True}))


# ── providers ────────────────────────────────────────────────────────────────


def test_direct_provider_base_url_and_auth_header(respx_mock: respx.MockRouter) -> None:
    route = _ok(respx_mock)
    with SkyLink(api_key="sk_direct", environ={}) as sky:
        assert sky.base_url == DIRECT_BASE_URL
        sky.request("GET", "/weather/metar/KJFK")

    request = route.calls.last.request
    assert str(request.url) == "https://data.skylinkapi.com/v3.1/weather/metar/KJFK"
    assert request.headers["x-api-key"] == "sk_direct"
    assert "X-RapidAPI-Key" not in request.headers


def test_rapidapi_provider_base_url_and_headers(respx_mock: respx.MockRouter) -> None:
    route = _ok(respx_mock)
    with SkyLink(api_key="rapid_key", provider="rapidapi", environ={}) as sky:
        assert sky.base_url == RAPIDAPI_BASE_URL
        sky.request("GET", "/weather/metar/KJFK")

    request = route.calls.last.request
    # RapidAPI listing has no version prefix — the path is used as-is.
    assert str(request.url) == "https://skylink-api.p.rapidapi.com/weather/metar/KJFK"
    assert request.headers["x-rapidapi-key"] == "rapid_key"
    assert request.headers["x-rapidapi-host"] == RAPIDAPI_HOST
    assert "x-api-key" not in request.headers


def test_internal_backend_headers_are_never_sent(respx_mock: respx.MockRouter) -> None:
    route = _ok(respx_mock)
    with SkyLink(api_key="sk", environ={}) as sky:
        sky.request("GET", "/health")

    headers = route.calls.last.request.headers
    assert "x-internal-key" not in headers
    assert "x-rapidapi-proxy-secret" not in headers


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ValueError, match="provider must be one of"):
        resolve_config(api_key="k", provider="ftp", environ={})  # type: ignore[arg-type]


# ── credentials ──────────────────────────────────────────────────────────────


def test_api_key_from_env_direct() -> None:
    config = resolve_config(environ={"SKYLINK_API_KEY": "from-env"})
    assert config.api_key == "from-env"
    assert config.auth_headers() == {"x-api-key": "from-env"}


def test_api_key_from_env_rapidapi() -> None:
    config = resolve_config(provider="rapidapi", environ={"RAPIDAPI_KEY": "rapid-env"})
    assert config.api_key == "rapid-env"
    assert config.auth_headers() == {
        "X-RapidAPI-Key": "rapid-env",
        "X-RapidAPI-Host": RAPIDAPI_HOST,
    }


def test_env_var_is_provider_specific() -> None:
    # A direct key must not leak into the RapidAPI channel.
    with pytest.raises(AuthenticationError):
        resolve_config(provider="rapidapi", environ={"SKYLINK_API_KEY": "direct-only"})


def test_os_environ_is_used_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKYLINK_API_KEY", "ambient")
    assert resolve_config().api_key == "ambient"


def test_explicit_key_beats_env() -> None:
    config = resolve_config(api_key="explicit", environ={"SKYLINK_API_KEY": "from-env"})
    assert config.api_key == "explicit"


def test_blank_env_key_is_treated_as_missing() -> None:
    with pytest.raises(AuthenticationError):
        resolve_config(environ={"SKYLINK_API_KEY": "   "})


def test_missing_key_raises_authentication_error() -> None:
    with pytest.raises(AuthenticationError) as excinfo:
        resolve_config(environ={})
    assert "SKYLINK_API_KEY" in str(excinfo.value)
    assert excinfo.value.status_code == 401


# ── base URL override ────────────────────────────────────────────────────────


def test_explicit_base_url_without_key_is_allowed(respx_mock: respx.MockRouter) -> None:
    """Staging runs with DISABLE_AUTH=true — a missing key must not be fatal."""

    route = _ok(respx_mock)
    with SkyLink(base_url=STAGING_URL, environ={}) as sky:
        assert sky.api_key is None
        sky.request("GET", "/weather/metar/KJFK")

    request = route.calls.last.request
    assert str(request.url) == f"{STAGING_URL}/weather/metar/KJFK"
    assert "x-api-key" not in request.headers


def test_custom_base_url_does_not_duplicate_version(respx_mock: respx.MockRouter) -> None:
    route = _ok(respx_mock)
    with SkyLink(api_key="k", base_url=STAGING_URL, environ={}) as sky:
        sky.request("GET", "/adsb/aircraft")

    url = str(route.calls.last.request.url)
    assert url == "http://localhost:8081/v3.1/adsb/aircraft"
    assert url.count("/v3.1") == 1


def test_trailing_slash_is_stripped_from_base_url(respx_mock: respx.MockRouter) -> None:
    route = _ok(respx_mock)
    with SkyLink(api_key="k", base_url="http://localhost:8081/v3.1///", environ={}) as sky:
        sky.request("GET", "/countries")

    assert str(route.calls.last.request.url) == "http://localhost:8081/v3.1/countries"


def test_path_without_leading_slash_still_joins(respx_mock: respx.MockRouter) -> None:
    route = _ok(respx_mock)
    with SkyLink(api_key="k", environ={}) as sky:
        sky.request("GET", "countries")

    assert str(route.calls.last.request.url) == f"{DIRECT_BASE_URL}/countries"


def test_trailing_slash_in_path_is_preserved(respx_mock: respx.MockRouter) -> None:
    """``/adsb/`` genuinely needs its trailing slash."""

    route = _ok(respx_mock)
    with SkyLink(api_key="k", environ={}) as sky:
        sky.request("GET", "/adsb/")

    assert str(route.calls.last.request.url) == f"{DIRECT_BASE_URL}/adsb/"


def test_empty_base_url_rejected() -> None:
    with pytest.raises(ValueError, match="base_url must not be empty"):
        resolve_config(api_key="k", base_url="/", environ={})


# ── headers ──────────────────────────────────────────────────────────────────


def test_user_agent(respx_mock: respx.MockRouter) -> None:
    route = _ok(respx_mock)
    with SkyLink(api_key="k", environ={}) as sky:
        sky.request("GET", "/health")

    assert f"skylink-api-python/{__version__}" == USER_AGENT
    assert route.calls.last.request.headers["user-agent"] == USER_AGENT


def test_default_headers_and_per_request_override(respx_mock: respx.MockRouter) -> None:
    route = _ok(respx_mock)
    with SkyLink(api_key="k", default_headers={"X-Trace": "abc"}, environ={}) as sky:
        sky.request("GET", "/health")
        sky.request("GET", "/health", options={"headers": {"X-Trace": "override"}})

    assert route.calls[0].request.headers["x-trace"] == "abc"
    assert route.calls[1].request.headers["x-trace"] == "override"


def test_default_headers_can_override_user_agent(respx_mock: respx.MockRouter) -> None:
    route = _ok(respx_mock)
    with SkyLink(api_key="k", default_headers={"User-Agent": "my-app/1.0"}, environ={}) as sky:
        sky.request("GET", "/health")

    assert route.calls.last.request.headers["user-agent"] == "my-app/1.0"


def test_accept_header_matches_response_kind(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.route().mock(return_value=httpx.Response(200, content=b"%PDF-1.4"))
    with SkyLink(api_key="k", environ={}) as sky:
        sky.request("GET", "/briefing/pdf", response_kind="bytes")

    assert route.calls.last.request.headers["accept"] == "*/*"


# ── misc options ─────────────────────────────────────────────────────────────


def test_timeout_defaults_and_overrides() -> None:
    assert resolve_config(api_key="k", environ={}).timeout == DEFAULT_TIMEOUT
    assert resolve_config(api_key="k", timeout=2.5, environ={}).timeout == httpx.Timeout(2.5)
    assert resolve_config(api_key="k", timeout=None, environ={}).timeout is None
    custom = httpx.Timeout(1.0, read=9.0)
    assert resolve_config(api_key="k", timeout=custom, environ={}).timeout == custom


def test_per_request_timeout_reaches_httpx(respx_mock: respx.MockRouter) -> None:
    route = _ok(respx_mock)
    with SkyLink(api_key="k", environ={}) as sky:
        sky.request("GET", "/health", options={"timeout": 1.5})

    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout == {"connect": 1.5, "read": 1.5, "write": 1.5, "pool": 1.5}


def test_max_retries_defaults_and_validation() -> None:
    assert resolve_config(api_key="k", environ={}).max_retries == DEFAULT_MAX_RETRIES
    assert resolve_config(api_key="k", max_retries=0, environ={}).max_retries == 0
    with pytest.raises(ValueError, match="max_retries must be >= 0"):
        resolve_config(api_key="k", max_retries=-1, environ={})


def test_history_plan_default_and_override() -> None:
    assert resolve_config(api_key="k", environ={}).history_plan == "ultra"
    assert resolve_config(api_key="k", history_plan="mega", environ={}).history_plan == "mega"
    with pytest.raises(ValueError, match="history_plan must be one of"):
        resolve_config(api_key="k", history_plan="turbo", environ={})  # type: ignore[arg-type]


def test_client_exposes_config() -> None:
    with SkyLink(api_key="k", history_plan="mega", max_retries=1, environ={}) as sky:
        assert sky.provider == "direct"
        assert sky.history_plan == "mega"
        assert sky.max_retries == 1
        assert sky.api_key == "k"
        assert sky.last_rate_limit is None


# ── request() escape hatch and lifecycle ─────────────────────────────────────


def test_request_sends_query_and_json_body(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.route().mock(return_value=httpx.Response(201, json={"id": "wh_1"}))
    with SkyLink(api_key="k", environ={}) as sky:
        created = sky.request(
            "post",
            "/webhooks",
            query={"dry_run": False},
            json_body={"url": "https://example.com/hook"},
        )

    assert created == {"id": "wh_1"}
    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.params["dry_run"] == "false"
    assert json.loads(request.content) == {"url": "https://example.com/hook"}
    assert request.headers["content-type"] == "application/json"


def test_context_manager_closes_transport() -> None:
    sky = SkyLink(api_key="k", environ={})
    with sky as entered:
        assert entered is sky
    assert sky.http_client.is_closed


def test_custom_http_client_is_used(respx_mock: respx.MockRouter) -> None:
    route = _ok(respx_mock)
    http_client = httpx.Client(headers={"X-Custom-Client": "yes"})
    with SkyLink(api_key="k", http_client=http_client, environ={}) as sky:
        assert sky.http_client is http_client
        sky.request("GET", "/health")

    assert route.calls.last.request.headers["x-custom-client"] == "yes"


async def test_async_client_basics(respx_mock: respx.MockRouter) -> None:
    route = _ok(respx_mock)
    async with AsyncSkyLink(api_key="async-key", environ={}) as sky:
        payload = await sky.request("GET", "/health")

    assert payload == {"ok": True}
    request = route.calls.last.request
    assert request.headers["x-api-key"] == "async-key"
    assert request.headers["user-agent"] == USER_AGENT


async def test_async_client_closes() -> None:
    sky = AsyncSkyLink(api_key="k", environ={})
    async with sky:
        pass
    assert sky.http_client.is_closed
