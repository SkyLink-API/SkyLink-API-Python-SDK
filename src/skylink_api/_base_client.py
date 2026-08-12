"""Transport layer: URL/header assembly, retry policy, sync + async request loops."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from types import TracebackType
from typing import Any, TypeVar

import httpx

from ._config import ClientConfig
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
    parse_rate_limit,
    parse_retry_after,
    process_response,
)
from ._types import RequestOptions, RequestSpec

__all__ = ["AsyncAPIClient", "BaseClient", "SyncAPIClient"]

_SyncSelfT = TypeVar("_SyncSelfT", bound="SyncAPIClient")
_AsyncSelfT = TypeVar("_AsyncSelfT", bound="AsyncAPIClient")

_ACCEPT_BY_KIND = {
    "json": "application/json",
    "none": "application/json",
    "text": "text/plain, application/json;q=0.9, */*;q=0.8",
    "bytes": "*/*",
}


async def _default_async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class BaseClient:
    """Everything the sync and async clients share except the I/O itself."""

    def __init__(self, config: ClientConfig) -> None:
        self._config = config
        #: Quota snapshot from the most recent successful response, if the gateway
        #: sent ``X-RateLimit-Requests-*`` headers.
        self.last_rate_limit: RateLimitInfo | None = None

    # ── introspection ────────────────────────────────────────────────────────

    @property
    def config(self) -> ClientConfig:
        return self._config

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

    def _record_rate_limit(self, response: httpx.Response) -> None:
        rate_limit = parse_rate_limit(response.headers)
        if rate_limit is not None:
            self.last_rate_limit = rate_limit


class SyncAPIClient(BaseClient):
    """Blocking transport built on a single :class:`httpx.Client`."""

    def __init__(
        self,
        config: ClientConfig,
        *,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(config)
        self._sleep = sleep
        self._http_client = http_client or httpx.Client(
            timeout=config.timeout,
            follow_redirects=True,
        )

    @property
    def http_client(self) -> httpx.Client:
        return self._http_client

    def execute(self, spec: RequestSpec, options: RequestOptions | None = None) -> Any:
        """Send ``spec``, retrying per policy, and decode the response."""

        max_retries = self._resolve_max_retries(options)
        attempt = 0

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

            if response.is_success:
                self._record_rate_limit(response)
                return process_response(spec, response)

            retryable = self._should_retry_status(spec.method, response.status_code)
            if attempt < max_retries and retryable:
                delay = self._retry_delay(attempt, response.headers)
                response.close()
                self._sleep(delay)
                attempt += 1
                continue

            raise build_status_error(response)

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""

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
    ) -> None:
        super().__init__(config)
        self._sleep = sleep
        self._http_client = http_client or httpx.AsyncClient(
            timeout=config.timeout,
            follow_redirects=True,
        )

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._http_client

    async def execute(self, spec: RequestSpec, options: RequestOptions | None = None) -> Any:
        """Send ``spec``, retrying per policy, and decode the response."""

        max_retries = self._resolve_max_retries(options)
        attempt = 0

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

            if response.is_success:
                self._record_rate_limit(response)
                return process_response(spec, response)

            retryable = self._should_retry_status(spec.method, response.status_code)
            if attempt < max_retries and retryable:
                delay = self._retry_delay(attempt, response.headers)
                await response.aclose()
                await self._sleep(delay)
                attempt += 1
                continue

            raise build_status_error(response)

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""

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
