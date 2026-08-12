"""Transport layer: URL/header assembly, retry policy, cache, quota hooks, request loops."""

from __future__ import annotations

import asyncio
import random
import threading
import time
import warnings
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, TypeVar

import httpx

from ._config import ClientConfig, mask_api_key
from ._constants import (
    INITIAL_RETRY_DELAY,
    MAX_RETRY_DELAY,
    RETRYABLE_STATUS_CODES,
    UNSAFE_METHODS,
    USER_AGENT,
)
from ._exceptions import APIConnectionError, APITimeoutError
from ._qs import build_query
from ._response import (
    RateLimitInfo,
    build_status_error,
    coerce_payload,
    decode_payload,
    parse_rate_limit,
    parse_retry_after,
)
from ._types import RequestOptions, RequestSpec
from .helpers.cache import CacheProtocol, cache_key, operation_name

__all__ = ["AsyncAPIClient", "BaseClient", "SyncAPIClient"]

_SyncSelfT = TypeVar("_SyncSelfT", bound="SyncAPIClient")
_AsyncSelfT = TypeVar("_AsyncSelfT", bound="AsyncAPIClient")

_ACCEPT_BY_KIND = {
    "json": "application/json",
    "none": "application/json",
    "text": "text/plain, application/json;q=0.9, */*;q=0.8",
    "bytes": "*/*",
}

#: Distinguishes "not cached" from "cached ``None``" (``DELETE`` bodies, empty 200s).
_MISS: Any = object()

#: Callback signatures for the quota hooks.
RateLimitCallback = Callable[[RateLimitInfo], None]
Unsubscribe = Callable[[], None]


async def _default_async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


@dataclass(frozen=True)
class _CachePlan:
    """Where a response would be cached and for how long."""

    key: str
    ttl: float


@dataclass
class _QuotaWatch:
    """One ``on_quota_low`` registration plus its edge-trigger state."""

    callback: RateLimitCallback
    threshold: float
    triggered: bool = False


@dataclass
class _Hooks:
    """Hook registry, shared shape for both clients."""

    rate_limit: list[RateLimitCallback] = field(default_factory=list)
    quota_low: list[_QuotaWatch] = field(default_factory=list)


class BaseClient:
    """Everything the sync and async clients share except the I/O itself."""

    def __init__(self, config: ClientConfig, *, cache: CacheProtocol | None = None) -> None:
        if cache is not None and not isinstance(cache, CacheProtocol):
            raise TypeError(
                "cache must implement get(key) and set(key, value, ttl) — see "
                "skylink_api.helpers.cache.CacheProtocol"
            )
        self._config = config
        self._cache = cache
        self._hooks = _Hooks()
        self._hook_lock = threading.Lock()
        #: Quota snapshot from the most recent response that carried quota
        #: headers — ``X-RateLimit-Requests-*`` on RapidAPI, ``X-RateLimit-*``
        #: on the direct channel.
        #:
        #: Last writer wins, so with concurrent requests in flight this is "one of
        #: the recent responses", not "the newest". Use :meth:`on_rate_limit` when
        #: you need the value that belongs to a specific response.
        self.last_rate_limit: RateLimitInfo | None = None

    # ── introspection ────────────────────────────────────────────────────────

    @property
    def config(self) -> ClientConfig:
        return self._config

    @property
    def cache(self) -> CacheProtocol | None:
        """The response cache, or ``None`` when caching is off (the default)."""

        return self._cache

    def __repr__(self) -> str:
        """Short, informative and key-safe — the API key is always masked."""

        return (
            f"{type(self).__name__}(provider={self._config.provider!r}, "
            f"base_url={self._config.base_url!r}, "
            f"api_key={mask_api_key(self._config.api_key)!r}, "
            f"max_retries={self._config.max_retries}, "
            f"history_plan={self._config.history_plan!r}, "
            f"cache={'on' if self._cache is not None else 'off'})"
        )

    @property
    def base_url(self) -> str:
        return self._config.base_url

    @property
    def provider(self) -> str:
        return self._config.provider

    @property
    def max_retries(self) -> int:
        return self._config.max_retries

    @property
    def history_plan(self) -> str:
        return self._config.history_plan

    # ── request assembly ─────────────────────────────────────────────────────

    def _build_url(self, path: str) -> str:
        """Join ``base_url`` and a resource path.

        ``base_url`` is stored without a trailing slash and paths are version
        independent, so a custom base URL is never given an extra ``/v3.1``.
        """

        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self._config.base_url}{path}"

    def _build_headers(self, spec: RequestSpec, options: RequestOptions | None) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": _ACCEPT_BY_KIND.get(spec.response_kind, "application/json"),
            "User-Agent": USER_AGENT,
        }
        headers.update(self._config.auth_headers())
        headers.update(self._config.default_headers)
        if spec.headers:
            headers.update(spec.headers)
        if options is not None and options.get("headers"):
            headers.update(options["headers"])
        return headers

    def _build_params(self, spec: RequestSpec, options: RequestOptions | None) -> dict[str, str]:
        extra = options.get("query") if options is not None else None
        return build_query(spec.query, extra)

    def _resolve_max_retries(self, options: RequestOptions | None) -> int:
        if options is not None and "max_retries" in options:
            value = options["max_retries"]
            if value < 0:
                raise ValueError(f"max_retries must be >= 0, got {value}")
            return value
        return self._config.max_retries

    def _request_kwargs(self, spec: RequestSpec, options: RequestOptions | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "method": spec.method,
            "url": self._build_url(spec.path),
            "params": self._build_params(spec, options),
            "headers": self._build_headers(spec, options),
        }
        if spec.json_body is not None:
            kwargs["json"] = spec.json_body
        if options is not None and "timeout" in options:
            kwargs["timeout"] = options["timeout"]
        return kwargs

    # ── retry policy ─────────────────────────────────────────────────────────

    def _should_retry_status(self, method: str, status_code: int) -> bool:
        """429/500/502/503/504 are retried; unsafe methods only on 429.

        A 429 provably never reached the handler, so replaying ``POST /webhooks``
        after one cannot duplicate a subscription — a 5xx can.
        """

        if status_code not in RETRYABLE_STATUS_CODES:
            return False
        if method.upper() in UNSAFE_METHODS:
            return status_code == 429
        return True

    def _should_retry_transport(self, method: str) -> bool:
        """Connection/timeout failures are ambiguous — never replay unsafe methods."""

        return method.upper() not in UNSAFE_METHODS

    def _retry_delay(self, attempt: int, headers: Mapping[str, str] | None = None) -> float:
        """``Retry-After`` if the server sent one, else full-jitter exponential backoff.

        ``attempt`` is 0-based, so the delay before the first retry is drawn from
        ``[0, 0.5)`` seconds and the cap is reached at attempt 4.
        """

        if headers is not None:
            retry_after = parse_retry_after(headers)
            if retry_after is not None:
                return retry_after
        ceiling = min(MAX_RETRY_DELAY, INITIAL_RETRY_DELAY * float(2**attempt))
        return float(random.random() * ceiling)

    # ── quota hooks ──────────────────────────────────────────────────────────

    def on_rate_limit(self, callback: RateLimitCallback) -> Unsubscribe:
        """Call ``callback(info)`` after every response that carried quota headers.

        ``info`` is the snapshot parsed from *that* response, not
        :attr:`last_rate_limit` — with concurrent requests the attribute is
        last-writer-wins, the callback argument never is::

            stop = sky.on_rate_limit(lambda info: print(info.remaining, "left"))
            ...
            stop()   # unsubscribe

        Both channels' spellings are understood (``X-RateLimit-Requests-*`` on
        RapidAPI, ``X-RateLimit-*`` on direct); a response carrying neither
        (staging instances, some proxies) fires nothing. Error responses fire too
        — a 429 carries the most interesting quota numbers of all.

        An exception raised by ``callback`` never fails the request: it is caught
        and re-emitted as a :class:`RuntimeWarning`, so it shows up in test runs
        and in ``-W error`` setups without breaking production traffic.

        Returns:
            A function that removes the callback; calling it twice is harmless.
        """

        with self._hook_lock:
            self._hooks.rate_limit.append(callback)

        def unsubscribe() -> None:
            with self._hook_lock:
                if callback in self._hooks.rate_limit:
                    self._hooks.rate_limit.remove(callback)

        return unsubscribe

    def on_quota_low(
        self,
        callback: RateLimitCallback,
        *,
        threshold: float = 0.1,
    ) -> Unsubscribe:
        """Call ``callback(info)`` when ``remaining / limit`` drops to ``threshold``.

        Edge triggered, not level triggered: it fires **once** on the crossing and
        stays quiet until the ratio climbs back above ``threshold`` (a new quota
        window), which then re-arms it. Otherwise every remaining call of the day
        would fire the callback.

        Responses that carry no ``limit``/``remaining`` pair, or a ``limit`` of
        zero, are ignored — a ratio cannot be computed from them.

        Args:
            callback: Receives the :class:`RateLimitInfo` of the crossing response.
            threshold: Fraction of the quota left, ``0 < threshold <= 1``
                (default ``0.1`` — the last 10%).

        Returns:
            A function that removes the callback.
        """

        if not 0 < threshold <= 1:
            raise ValueError(f"threshold must be in (0, 1], got {threshold}")

        watch = _QuotaWatch(callback=callback, threshold=threshold)
        with self._hook_lock:
            self._hooks.quota_low.append(watch)

        def unsubscribe() -> None:
            with self._hook_lock:
                if watch in self._hooks.quota_low:
                    self._hooks.quota_low.remove(watch)

        return unsubscribe

    def _record_rate_limit(self, response: httpx.Response) -> None:
        """Publish the quota headers of one response: attribute first, then hooks."""

        rate_limit = parse_rate_limit(response.headers)
        if rate_limit is None:
            return
        self.last_rate_limit = rate_limit
        self._dispatch_rate_limit(rate_limit)

    def _dispatch_rate_limit(self, info: RateLimitInfo) -> None:
        """Run the hooks for ``info``; user code never runs while the lock is held."""

        ratio: float | None = None
        if info.limit is not None and info.remaining is not None and info.limit > 0:
            ratio = info.remaining / info.limit

        with self._hook_lock:
            callbacks = list(self._hooks.rate_limit)
            due: list[RateLimitCallback] = []
            for watch in self._hooks.quota_low:
                if ratio is None:
                    continue
                if ratio <= watch.threshold:
                    if not watch.triggered:
                        watch.triggered = True
                        due.append(watch.callback)
                else:
                    watch.triggered = False

        for callback in [*callbacks, *due]:
            try:
                callback(info)
            except Exception as err:  # a bad hook must not fail the call
                warnings.warn(
                    f"SkyLink quota hook {getattr(callback, '__name__', callback)!r} "
                    f"raised {type(err).__name__}: {err}",
                    RuntimeWarning,
                    stacklevel=2,
                )

    def _copy_hooks_from(self, other: BaseClient) -> None:
        """Snapshot ``other``'s hooks into this client (used by ``with_options``)."""

        with other._hook_lock:
            rate_limit = list(other._hooks.rate_limit)
            quota_low = [
                _QuotaWatch(watch.callback, watch.threshold, watch.triggered)
                for watch in other._hooks.quota_low
            ]
        with self._hook_lock:
            self._hooks.rate_limit = rate_limit
            self._hooks.quota_low = quota_low

    # ── response cache ───────────────────────────────────────────────────────

    def _cache_plan(self, spec: RequestSpec, options: RequestOptions | None) -> _CachePlan | None:
        """Where this request would be cached, or ``None`` when it must not be.

        Only ``GET`` is cacheable — replaying anything else would be a lie — and a
        resolved TTL of ``0`` (the default for every operation) means "no caching",
        which is why an unconfigured cache changes nothing.
        """

        cache = self._cache
        if cache is None or spec.method.upper() != "GET":
            return None

        ttl = 0.0
        ttl_for = getattr(cache, "ttl_for", None)
        if callable(ttl_for):
            operation = spec.operation or operation_name(spec.path)
            try:
                ttl = float(ttl_for(operation))
            except Exception as err:  # a broken cache degrades to no cache
                self._warn_cache("ttl_for", err)
                return None
            if ttl <= 0:
                return None

        key = cache_key(
            spec.method,
            spec.path,
            self._build_params(spec, options),
            provider=self._config.provider,
            base_url=self._config.base_url,
        )
        return _CachePlan(key=key, ttl=ttl)

    def _cache_load(self, plan: _CachePlan, spec: RequestSpec) -> Any:
        """Cached payload for ``spec``, or :data:`_MISS`."""

        cache = self._cache
        if cache is None:
            return _MISS
        try:
            entry = cache.get(plan.key)
        except Exception as err:  # a broken cache degrades to no cache
            self._warn_cache("get", err)
            return _MISS
        if not isinstance(entry, Mapping) or entry.get("kind") != spec.response_kind:
            # Absent, or stored for a different decoding of the same URL.
            return _MISS
        return entry.get("payload")

    def _cache_store(self, plan: _CachePlan, spec: RequestSpec, payload: Any) -> None:
        cache = self._cache
        if cache is None:
            return
        try:
            cache.set(plan.key, {"kind": spec.response_kind, "payload": payload}, plan.ttl)
        except Exception as err:  # a broken cache degrades to no cache
            self._warn_cache("set", err)

    @staticmethod
    def _warn_cache(operation: str, err: Exception) -> None:
        warnings.warn(
            f"SkyLink response cache {operation}() raised {type(err).__name__}: {err} — "
            "continuing without the cache.",
            RuntimeWarning,
            stacklevel=3,
        )


class SyncAPIClient(BaseClient):
    """Blocking transport built on a single :class:`httpx.Client`."""

    def __init__(
        self,
        config: ClientConfig,
        *,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        cache: CacheProtocol | None = None,
        owns_transport: bool = True,
    ) -> None:
        super().__init__(config, cache=cache)
        self._sleep = sleep
        self._owns_transport = owns_transport
        self._http_client = http_client or httpx.Client(
            timeout=config.timeout,
            follow_redirects=True,
        )

    @property
    def http_client(self) -> httpx.Client:
        return self._http_client

    def execute(self, spec: RequestSpec, options: RequestOptions | None = None) -> Any:
        """Send ``spec``, retrying per policy, and decode the response.

        The response cache is consulted here and nowhere else: a hit skips the
        network entirely and re-validates the stored payload, a successful GET
        stores its payload before it is validated.
        """

        max_retries = self._resolve_max_retries(options)
        attempt = 0

        plan = self._cache_plan(spec, options)
        if plan is not None:
            payload = self._cache_load(plan, spec)
            if payload is not _MISS:
                return coerce_payload(spec, payload)

        while True:
            request = self._http_client.build_request(**self._request_kwargs(spec, options))
            try:
                response = self._http_client.send(request)
            except httpx.TimeoutException as err:
                if attempt < max_retries and self._should_retry_transport(spec.method):
                    self._sleep(self._retry_delay(attempt))
                    attempt += 1
                    continue
                raise APITimeoutError(f"Request timed out: {spec.method} {request.url}") from err
            except httpx.HTTPError as err:
                if attempt < max_retries and self._should_retry_transport(spec.method):
                    self._sleep(self._retry_delay(attempt))
                    attempt += 1
                    continue
                raise APIConnectionError(
                    f"Connection error for {spec.method} {request.url}: {err}"
                ) from err

            self._record_rate_limit(response)

            if response.is_success:
                payload = decode_payload(spec, response)
                if plan is not None:
                    self._cache_store(plan, spec, payload)
                return coerce_payload(spec, payload, status_code=response.status_code)

            retryable = self._should_retry_status(spec.method, response.status_code)
            if attempt < max_retries and retryable:
                delay = self._retry_delay(attempt, response.headers)
                response.close()
                self._sleep(delay)
                attempt += 1
                continue

            raise build_status_error(response)

    def close(self) -> None:
        """Close the underlying HTTP connection pool.

        A clone made by ``with_options`` shares its parent's transport and does
        **not** own it, so closing the clone is a no-op — only the client that
        created the pool (or was handed one at construction) closes it.
        """

        if self._owns_transport:
            self._http_client.close()

    def __enter__(self: _SyncSelfT) -> _SyncSelfT:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class AsyncAPIClient(BaseClient):
    """Asyncio transport built on a single :class:`httpx.AsyncClient`."""

    def __init__(
        self,
        config: ClientConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = _default_async_sleep,
        cache: CacheProtocol | None = None,
        owns_transport: bool = True,
    ) -> None:
        super().__init__(config, cache=cache)
        self._sleep = sleep
        self._owns_transport = owns_transport
        self._http_client = http_client or httpx.AsyncClient(
            timeout=config.timeout,
            follow_redirects=True,
        )

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._http_client

    async def execute(self, spec: RequestSpec, options: RequestOptions | None = None) -> Any:
        """Send ``spec``, retrying per policy, and decode the response.

        The response cache is consulted here and nowhere else — see
        :meth:`SyncAPIClient.execute`.
        """

        max_retries = self._resolve_max_retries(options)
        attempt = 0

        plan = self._cache_plan(spec, options)
        if plan is not None:
            payload = self._cache_load(plan, spec)
            if payload is not _MISS:
                return coerce_payload(spec, payload)

        while True:
            request = self._http_client.build_request(**self._request_kwargs(spec, options))
            try:
                response = await self._http_client.send(request)
            except httpx.TimeoutException as err:
                if attempt < max_retries and self._should_retry_transport(spec.method):
                    await self._sleep(self._retry_delay(attempt))
                    attempt += 1
                    continue
                raise APITimeoutError(f"Request timed out: {spec.method} {request.url}") from err
            except httpx.HTTPError as err:
                if attempt < max_retries and self._should_retry_transport(spec.method):
                    await self._sleep(self._retry_delay(attempt))
                    attempt += 1
                    continue
                raise APIConnectionError(
                    f"Connection error for {spec.method} {request.url}: {err}"
                ) from err

            self._record_rate_limit(response)

            if response.is_success:
                payload = decode_payload(spec, response)
                if plan is not None:
                    self._cache_store(plan, spec, payload)
                return coerce_payload(spec, payload, status_code=response.status_code)

            retryable = self._should_retry_status(spec.method, response.status_code)
            if attempt < max_retries and retryable:
                delay = self._retry_delay(attempt, response.headers)
                await response.aclose()
                await self._sleep(delay)
                attempt += 1
                continue

            raise build_status_error(response)

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool.

        A clone made by ``with_options`` does not own the transport it shares, so
        closing it leaves the pool alone — see :meth:`SyncAPIClient.close`.
        """

        if self._owns_transport:
            await self._http_client.aclose()

    async def __aenter__(self: _AsyncSelfT) -> _AsyncSelfT:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
