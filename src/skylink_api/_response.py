"""Turning an :class:`httpx.Response` into SDK values (and SDK errors)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from ._constants import (
    MAX_RETRY_AFTER,
    RATE_LIMIT_LIMIT_HEADER,
    RATE_LIMIT_REMAINING_HEADER,
    RATE_LIMIT_RESET_HEADER,
    RETRY_AFTER_HEADER,
)
from ._exceptions import APIResponseValidationError, APIStatusError, make_status_error
from ._types import RequestSpec

__all__ = [
    "RateLimitInfo",
    "build_status_error",
    "get_header",
    "parse_rate_limit",
    "parse_retry_after",
    "process_response",
]


def get_header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup that also works with plain dicts."""

    value = headers.get(name)
    if value is not None:
        return value
    lowered = name.lower()
    for key, candidate in headers.items():
        if key.lower() == lowered:
            return candidate
    return None


def _parse_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


@dataclass(frozen=True)
class RateLimitInfo:
    """Quota snapshot from the ``X-RateLimit-Requests-*`` response headers.

    The headers are injected by the marketplace gateway, so they are absent when
    talking to a staging instance directly.
    """

    limit: int | None = None
    remaining: int | None = None
    reset: int | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> RateLimitInfo | None:
        """Return ``None`` when the response carries no quota headers at all."""

        limit = _parse_int(get_header(headers, RATE_LIMIT_LIMIT_HEADER))
        remaining = _parse_int(get_header(headers, RATE_LIMIT_REMAINING_HEADER))
        reset = _parse_int(get_header(headers, RATE_LIMIT_RESET_HEADER))
        if limit is None and remaining is None and reset is None:
            return None
        return cls(limit=limit, remaining=remaining, reset=reset)


def parse_rate_limit(headers: Mapping[str, str]) -> RateLimitInfo | None:
    """Parse the quota headers; ``None`` when none of them are present."""

    return RateLimitInfo.from_headers(headers)


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """Parse ``Retry-After`` (delta-seconds or HTTP-date) into seconds.

    Negative or absurd values are clamped to ``[0, MAX_RETRY_AFTER]``; unparseable
    values yield ``None`` so the caller falls back to jittered backoff.
    """

    raw = get_header(headers, RETRY_AFTER_HEADER)
    if raw is None or not raw.strip():
        return None

    raw = raw.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            target = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        seconds = (target - datetime.now(timezone.utc)).total_seconds()

    return max(0.0, min(seconds, MAX_RETRY_AFTER))


_ADAPTERS: dict[Any, TypeAdapter[Any]] = {}


def _adapter_for(cast_to: Any) -> TypeAdapter[Any]:
    try:
        adapter = _ADAPTERS.get(cast_to)
    except TypeError:  # unhashable annotation
        return TypeAdapter(cast_to)
    if adapter is None:
        adapter = TypeAdapter(cast_to)
        _ADAPTERS[cast_to] = adapter
    return adapter


def _parse_json(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as err:
        raise APIResponseValidationError(
            "Expected a JSON response body but could not decode it.",
            status_code=response.status_code,
            body=response.text,
        ) from err


def process_response(spec: RequestSpec, response: httpx.Response) -> Any:
    """Decode a successful response according to ``spec.response_kind``/``cast_to``.

    ``json`` bodies are validated into ``spec.cast_to`` when given (any type pydantic
    understands: a model, ``list[Model]``, ``dict[str, Model]``, ...), otherwise the
    decoded JSON is returned untouched.
    """

    kind = spec.response_kind
    if kind == "none":
        return None
    if kind == "bytes":
        return response.content
    if kind == "text":
        return response.text

    data = _parse_json(response)
    if spec.cast_to is None or data is None:
        return data

    try:
        return _adapter_for(spec.cast_to).validate_python(data)
    except ValidationError as err:
        raise APIResponseValidationError(
            f"Response did not match the expected shape for {spec.method} {spec.path}: {err}",
            status_code=response.status_code,
            body=data,
        ) from err


def _error_body(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return response.text


def build_status_error(response: httpx.Response) -> APIStatusError:
    """Map an error response onto the matching :class:`APIStatusError` subclass."""

    headers = dict(response.headers)
    return make_status_error(
        response.status_code,
        headers=headers,
        body=_error_body(response),
        rate_limit=parse_rate_limit(response.headers),
        retry_after=parse_retry_after(response.headers),
    )
