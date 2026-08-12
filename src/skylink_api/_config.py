"""Client configuration: provider defaults, env fallback, auth headers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import get_args

import httpx

from ._constants import (
    DEFAULT_HISTORY_PLAN,
    DEFAULT_MAX_RETRIES,
    DEFAULT_PROVIDER,
    DEFAULT_TIMEOUT,
    DIRECT_API_KEY_ENV,
    DIRECT_API_KEY_HEADER,
    DIRECT_BASE_URL,
    RAPIDAPI_BASE_URL,
    RAPIDAPI_HOST,
    RAPIDAPI_HOST_HEADER,
    RAPIDAPI_KEY_ENV,
    RAPIDAPI_KEY_HEADER,
)
from ._exceptions import AuthenticationError
from ._types import NOT_GIVEN, HistoryPlan, NotGiven, NotGivenOr, Provider

__all__ = ["ClientConfig", "mask_api_key", "resolve_config"]

_PROVIDERS: tuple[str, ...] = get_args(Provider)
_HISTORY_PLANS: tuple[str, ...] = get_args(HistoryPlan)

_PROVIDER_BASE_URLS: dict[str, str] = {
    "direct": DIRECT_BASE_URL,
    "rapidapi": RAPIDAPI_BASE_URL,
}

#: Environment variables consulted for each channel, in order.
#:
#: ``RAPIDAPI_KEY`` is the canonical name for the (default) RapidAPI channel, but
#: ``SKYLINK_API_KEY`` is accepted as a neutral fallback so that ``SkyLink()`` finds
#: a key that was exported under the generic name. The reverse does **not** hold:
#: a RapidAPI subscription key is meaningless on ``data.skylinkapi.com``, so the
#: direct channel never reads ``RAPIDAPI_KEY``.
_PROVIDER_KEY_ENVS: dict[str, tuple[str, ...]] = {
    "direct": (DIRECT_API_KEY_ENV,),
    "rapidapi": (RAPIDAPI_KEY_ENV, DIRECT_API_KEY_ENV),
}


@dataclass(frozen=True)
class ClientConfig:
    """Fully resolved client settings — no further defaulting happens downstream."""

    provider: Provider
    base_url: str
    api_key: str | None
    timeout: httpx.Timeout | None
    max_retries: int
    history_plan: HistoryPlan
    default_headers: Mapping[str, str] = field(default_factory=dict)

    def auth_headers(self) -> dict[str, str]:
        """Channel specific auth headers.

        Direct sends ``x-api-key``; RapidAPI sends ``X-RapidAPI-Key`` plus the
        mandatory ``X-RapidAPI-Host``. Internal backend headers (``x-internal-key``,
        ``x-rapidapi-proxy-secret``) are deliberately never sent by this SDK.
        """

        if self.provider == "rapidapi":
            headers = {RAPIDAPI_HOST_HEADER: RAPIDAPI_HOST}
            if self.api_key:
                headers[RAPIDAPI_KEY_HEADER] = self.api_key
            return headers

        if self.api_key:
            return {DIRECT_API_KEY_HEADER: self.api_key}
        return {}

    def with_overrides(
        self,
        *,
        timeout: NotGivenOr[float | httpx.Timeout | None] = NOT_GIVEN,
        max_retries: NotGivenOr[int] = NOT_GIVEN,
        history_plan: NotGivenOr[HistoryPlan] = NOT_GIVEN,
        default_headers: NotGivenOr[Mapping[str, str]] = NOT_GIVEN,
    ) -> ClientConfig:
        """A copy with some settings replaced — the engine behind ``with_options``.

        Omitted arguments keep the current value; ``default_headers`` **replaces**
        the mapping rather than merging into it, so a clone can also drop headers.
        Every override is validated exactly as :func:`resolve_config` validates it,
        because a bad clone should fail where it is created, not on the next call.
        """

        if isinstance(max_retries, NotGiven):
            resolved_retries = self.max_retries
        else:
            resolved_retries = max_retries
            if resolved_retries < 0:
                raise ValueError(f"max_retries must be >= 0, got {resolved_retries}")

        if isinstance(history_plan, NotGiven):
            resolved_plan: HistoryPlan = self.history_plan
        else:
            if history_plan not in _HISTORY_PLANS:
                raise ValueError(
                    f"history_plan must be one of {_HISTORY_PLANS}, got {history_plan!r}"
                )
            resolved_plan = history_plan

        return replace(
            self,
            timeout=self.timeout if isinstance(timeout, NotGiven) else _resolve_timeout(timeout),
            max_retries=resolved_retries,
            history_plan=resolved_plan,
            default_headers=(
                self.default_headers
                if isinstance(default_headers, NotGiven)
                else dict(default_headers)
            ),
        )


def mask_api_key(api_key: str | None) -> str | None:
    """Redact a key for display: ``"abcd…wxyz"``, or ``"***"`` when it is short.

    Used by every ``__repr__`` in the SDK. Repr output ends up in logs, tracebacks
    and bug reports, so it must never carry a usable credential — but the first
    and last few characters are enough to tell two keys apart.
    """

    if api_key is None:
        return None
    stripped = api_key.strip()
    if len(stripped) <= 8:
        return "***"
    return f"{stripped[:4]}…{stripped[-4:]}"


def _key_from_env(env: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    """First non-blank value among ``names`` — a blank variable counts as unset."""

    for name in names:
        value = env.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _env_hint(names: tuple[str, ...]) -> str:
    """Human readable list of the variables a channel accepts."""

    if len(names) == 1:
        return names[0]
    return f"{names[0]} (or {', '.join(names[1:])})"


def _resolve_timeout(
    timeout: NotGivenOr[float | httpx.Timeout | None],
) -> httpx.Timeout | None:
    if isinstance(timeout, NotGiven):
        return DEFAULT_TIMEOUT
    if timeout is None:
        return None
    if isinstance(timeout, httpx.Timeout):
        return timeout
    return httpx.Timeout(timeout)


def resolve_config(
    *,
    api_key: str | None = None,
    provider: Provider | None = None,
    base_url: str | None = None,
    timeout: NotGivenOr[float | httpx.Timeout | None] = NOT_GIVEN,
    max_retries: int | None = None,
    history_plan: HistoryPlan | None = None,
    default_headers: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> ClientConfig:
    """Merge explicit options, environment variables and defaults.

    * ``provider`` defaults to ``"rapidapi"`` — the channel most subscriptions use.
    * ``api_key`` falls back to the environment: ``RAPIDAPI_KEY`` and then
      ``SKYLINK_API_KEY`` on the rapidapi channel, ``SKYLINK_API_KEY`` only on the
      direct one (a marketplace key is not a direct key, so it is never reused the
      other way round). A blank variable counts as unset.
    * ``base_url`` defaults to the provider's endpoint and is used verbatim when
      given — the ``/v3.1`` prefix is part of the *direct* default only, so a custom
      base URL never gets a version appended.
    * A missing key raises :class:`AuthenticationError`, **except** when an explicit
      ``base_url`` was supplied (staging instances run with ``DISABLE_AUTH=true``).
    """

    env = os.environ if environ is None else environ

    resolved_provider = provider or DEFAULT_PROVIDER
    if resolved_provider not in _PROVIDERS:
        raise ValueError(f"provider must be one of {_PROVIDERS}, got {resolved_provider!r}")

    resolved_plan = history_plan or DEFAULT_HISTORY_PLAN
    if resolved_plan not in _HISTORY_PLANS:
        raise ValueError(f"history_plan must be one of {_HISTORY_PLANS}, got {resolved_plan!r}")

    key_envs = _PROVIDER_KEY_ENVS[resolved_provider]
    resolved_key = api_key.strip() or None if api_key is not None else _key_from_env(env, key_envs)

    if base_url is not None:
        resolved_base_url = base_url.rstrip("/")
        if not resolved_base_url:
            raise ValueError("base_url must not be empty")
    else:
        resolved_base_url = _PROVIDER_BASE_URLS[resolved_provider]
        if resolved_key is None:
            raise AuthenticationError(
                "No API key provided. Pass api_key=... to the client or set the "
                f"{_env_hint(key_envs)} environment variable. "
                "(Not required when you pass an explicit base_url, e.g. a staging "
                "instance running with DISABLE_AUTH=true.)"
            )

    resolved_retries = DEFAULT_MAX_RETRIES if max_retries is None else max_retries
    if resolved_retries < 0:
        raise ValueError(f"max_retries must be >= 0, got {resolved_retries}")

    return ClientConfig(
        provider=resolved_provider,
        base_url=resolved_base_url,
        api_key=resolved_key,
        timeout=_resolve_timeout(timeout),
        max_retries=resolved_retries,
        history_plan=resolved_plan,
        default_headers=dict(default_headers or {}),
    )
