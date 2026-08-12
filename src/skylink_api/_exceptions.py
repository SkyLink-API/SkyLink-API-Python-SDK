"""Exception hierarchy and the parser for the API's three error body shapes.

The backend never installs a custom exception handler, so exactly three shapes can
come back:

A. ``401`` from the auth middleware::

       {"error": "Unauthorized", "message": "...", "code": "MARKETPLACE_ACCESS_REQUIRED"}

B. any ``HTTPException`` (400/403/404/422/500/503)::

       {"detail": "Airport not found"}

C. FastAPI request validation (``422``)::

       {"detail": [{"loc": ["query", "icao"], "msg": "...", "type": "..."}]}

Anything else (HTML from a proxy, empty body, plain text) degrades to a generic
``HTTP <status>`` message with the raw payload kept on ``.body``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from ._response import RateLimitInfo

__all__ = [
    "APIConnectionError",
    "APIResponseValidationError",
    "APIStatusError",
    "APITimeoutError",
    "AuthenticationError",
    "BadRequestError",
    "InternalServerError",
    "NotFoundError",
    "ParsedErrorBody",
    "PermissionDeniedError",
    "RateLimitError",
    "ServiceUnavailableError",
    "SkyLinkError",
    "UnprocessableEntityError",
    "ValidationErrorItem",
    "make_status_error",
    "parse_error_body",
]

_MAX_MESSAGE_LENGTH = 500


class SkyLinkError(Exception):
    """Base class for every error raised by this SDK."""


class APIConnectionError(SkyLinkError):
    """The request never produced an HTTP response (DNS, TLS, refused, reset)."""

    def __init__(self, message: str = "Connection error.") -> None:
        super().__init__(message)
        self.message = message


class APITimeoutError(APIConnectionError):
    """The request exceeded the configured connect/read/write timeout."""

    def __init__(self, message: str = "Request timed out.") -> None:
        super().__init__(message)


class APIResponseValidationError(SkyLinkError):
    """A 2xx response could not be parsed into the expected shape.

    SkyLink serves scraped data, so this usually means the upstream payload
    changed. The raw payload is kept on ``.body`` for debugging.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: object = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class ValidationErrorItem:
    """One entry of a FastAPI ``422`` validation payload."""

    loc: tuple[str | int, ...]
    msg: str
    type: str

    def __str__(self) -> str:
        location = ".".join(str(part) for part in self.loc) or "body"
        return f"{location}: {self.msg}"


@dataclass(frozen=True)
class ParsedErrorBody:
    """Normalised view over any of the three error body shapes."""

    message: str
    code: str | None = None
    errors: tuple[ValidationErrorItem, ...] = field(default=())


class APIStatusError(SkyLinkError):
    """A non-2xx HTTP response.

    Attributes:
        status_code: HTTP status of the response.
        headers: response headers (lowercase keys, as normalised by httpx).
        body: parsed JSON body when the payload was JSON, otherwise the raw text.
        code: machine readable code from the gateway's 401 payload, if any.
        errors: parsed items of a ``422`` validation payload, if any.
    """

    default_status_code: ClassVar[int] = 0

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        headers: Mapping[str, str] | None = None,
        body: object = None,
        code: str | None = None,
        errors: Iterable[ValidationErrorItem] = (),
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code if status_code is not None else self.default_status_code
        self.headers: dict[str, str] = dict(headers or {})
        self.body = body
        self.code = code
        self.errors: tuple[ValidationErrorItem, ...] = tuple(errors)


class BadRequestError(APIStatusError):
    """400 — malformed or contradictory parameters (v2/v3 style input errors)."""

    default_status_code = 400


class AuthenticationError(APIStatusError):
    """401 — missing or rejected API key.

    Also raised at construction time when no key could be resolved and no explicit
    ``base_url`` was given.
    """

    default_status_code = 401


class PermissionDeniedError(APIStatusError):
    """403 — the key is valid but the plan does not allow this call."""

    default_status_code = 403


class NotFoundError(APIStatusError):
    """404 — unknown route or unknown identifier."""

    default_status_code = 404


class UnprocessableEntityError(APIStatusError):
    """422 — request validation failed; see ``.errors`` for per-field details."""

    default_status_code = 422


class RateLimitError(APIStatusError):
    """429 — marketplace quota exhausted or too many requests per second."""

    default_status_code = 429

    def __init__(
        self,
        message: str,
        *,
        rate_limit: RateLimitInfo | None = None,
        retry_after: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        #: Quota snapshot from the ``X-RateLimit-Requests-*`` headers, if present.
        self.rate_limit = rate_limit
        #: ``Retry-After`` in seconds (HTTP-dates are converted), capped at 60s.
        self.retry_after = retry_after


class InternalServerError(APIStatusError):
    """5xx — unexpected failure on the SkyLink side."""

    default_status_code = 500


class ServiceUnavailableError(InternalServerError):
    """503 — an upstream data source is not ready (routes cache, history DB, ...)."""

    default_status_code = 503


_STATUS_ERROR_CLASSES: dict[int, type[APIStatusError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    422: UnprocessableEntityError,
    429: RateLimitError,
    500: InternalServerError,
    503: ServiceUnavailableError,
}


def _as_validation_item(raw: Mapping[str, Any]) -> ValidationErrorItem:
    loc = raw.get("loc")
    parts: tuple[str | int, ...]
    if isinstance(loc, Sequence) and not isinstance(loc, (str, bytes)):
        parts = tuple(part if isinstance(part, int) else str(part) for part in loc)
    elif loc is None:
        parts = ()
    else:
        parts = (str(loc),)
    msg = raw.get("msg")
    type_ = raw.get("type")
    return ValidationErrorItem(
        loc=parts,
        msg=str(msg) if msg is not None else "",
        type=str(type_) if type_ is not None else "",
    )


def parse_error_body(body: object, *, status_code: int) -> ParsedErrorBody:
    """Normalise any of the three documented error bodies (plus junk) into one shape."""

    fallback = f"HTTP {status_code}"

    if isinstance(body, Mapping):
        detail = body.get("detail")

        # Shape C — validation errors.
        if isinstance(detail, Sequence) and not isinstance(detail, (str, bytes)):
            items = tuple(
                _as_validation_item(entry) for entry in detail if isinstance(entry, Mapping)
            )
            if items:
                summary = "; ".join(str(item) for item in items)
                message = f"Validation error: {summary}"[:_MAX_MESSAGE_LENGTH]
                return ParsedErrorBody(message, errors=items)

        # Shape B — plain HTTPException detail.
        if isinstance(detail, str) and detail.strip():
            return ParsedErrorBody(detail.strip()[:_MAX_MESSAGE_LENGTH])

        # Shape A — gateway envelope.
        code = body.get("code")
        code_str = code if isinstance(code, str) and code else None
        for key in ("message", "error"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return ParsedErrorBody(value.strip()[:_MAX_MESSAGE_LENGTH], code=code_str)
        if code_str is not None:
            return ParsedErrorBody(f"{fallback}: {code_str}", code=code_str)

    if isinstance(body, str) and body.strip():
        return ParsedErrorBody(f"{fallback}: {body.strip()[:_MAX_MESSAGE_LENGTH]}")

    return ParsedErrorBody(fallback)


def make_status_error(
    status_code: int,
    *,
    headers: Mapping[str, str] | None = None,
    body: object = None,
    rate_limit: RateLimitInfo | None = None,
    retry_after: float | None = None,
) -> APIStatusError:
    """Build the most specific :class:`APIStatusError` subclass for ``status_code``."""

    parsed = parse_error_body(body, status_code=status_code)
    error_cls = _STATUS_ERROR_CLASSES.get(status_code)
    if error_cls is None:
        error_cls = InternalServerError if status_code >= 500 else APIStatusError

    if issubclass(error_cls, RateLimitError):
        return error_cls(
            parsed.message,
            rate_limit=rate_limit,
            retry_after=retry_after,
            status_code=status_code,
            headers=headers,
            body=body,
            code=parsed.code,
            errors=parsed.errors,
        )

    return error_cls(
        parsed.message,
        status_code=status_code,
        headers=headers,
        body=body,
        code=parsed.code,
        errors=parsed.errors,
    )
