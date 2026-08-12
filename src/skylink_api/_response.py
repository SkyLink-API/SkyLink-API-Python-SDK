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
    RATE_LIMIT_HEADER_SETS,
    RETRY_AFTER_HEADER,
)
from ._exceptions import APIResponseValidationError, APIStatusError, make_status_error
from ._types import RequestSpec

__all__ = [
    "RateLimitInfo",
    "build_status_error",
    "coerce_payload",
    "decode_payload",
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
    """Quota snapshot from a response's rate-limit headers.

    Both gateway spellings are understood, because the two channels differ:
    RapidAPI sends ``X-RateLimit-Requests-{Limit,Remaining,Reset}``, the direct
    gateway sends ``X-RateLimit-{Limit,Remaining,Reset}``. A staging instance
    behind neither gateway sends no quota headers at all, and then there is
    nothing to report.
    """

    limit: int | None = None
    """Requests allowed in the current window."""

    remaining: int | None = None
    """Requests left in the current window."""

    reset: int | None = None
    """Seconds until the window rolls over."""

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> RateLimitInfo | None:
        """Return ``None`` when the response carries no quota headers at all.

        :data:`~skylink_api._constants.RATE_LIMIT_HEADER_SETS` is walked in
        priority order and the first family carrying a parseable number wins, so
        on RapidAPI — which sends several families at once — the plan's request
        quota is reported rather than the marketplace's free-tier ceiling.
        """

        for limit_header, remaining_header, reset_header in RATE_LIMIT_HEADER_SETS:
            limit = _parse_int(get_header(headers, limit_header))
            remaining = _parse_int(get_header(headers, remaining_header))
            reset = _parse_int(get_header(headers, reset_header))
            if limit is None and remaining is None and reset is None:
                continue
            return cls(limit=limit, remaining=remaining, reset=reset)
        return None


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


def decode_payload(spec: RequestSpec, response: httpx.Response) -> Any:
    """Turn the body into a plain Python payload — **before** ``cast_to`` validation.

    ``json`` → decoded JSON, ``text`` → ``str``, ``bytes`` → ``bytes``, ``none`` →
    ``None``. Split out from :func:`process_response` so the response cache can
    store this value: it is JSON-shaped (so an off-process store can serialise it)
    and validating it again on every hit hands each caller a fresh model instead of
    a shared, mutable one.
    """

    kind = spec.response_kind
    if kind == "none":
        return None
    if kind == "bytes":
        return response.content
    if kind == "text":
        return response.text
    return _parse_json(response)


def coerce_payload(spec: RequestSpec, payload: Any, *, status_code: int = 200) -> Any:
    """Validate a decoded payload into ``spec.cast_to``.

    ``json`` bodies are validated into ``spec.cast_to`` when given (any type pydantic
    understands: a model, ``list[Model]``, ``dict[str, Model]``, ...), otherwise the
    payload is returned untouched. ``status_code`` only decorates the error raised on
    a mismatch, so a replay from cache can report the status it was stored with.
    """

    if spec.response_kind != "json" or spec.cast_to is None or payload is None:
        return payload

    try:
        return _adapter_for(spec.cast_to).validate_python(payload)
    except ValidationError as err:
        raise APIResponseValidationError(
            f"Response did not match the expected shape for {spec.method} {spec.path}: {err}",
            status_code=status_code,
            body=payload,
        ) from err


def process_response(spec: RequestSpec, response: httpx.Response) -> Any:
    """Decode a successful response according to ``spec.response_kind``/``cast_to``."""

    payload = decode_payload(spec, response)
    return coerce_payload(spec, payload, status_code=response.status_code)


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
