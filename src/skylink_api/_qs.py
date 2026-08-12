"""Query string serialisation.

The API is inconsistent about input dates on purpose (each endpoint mirrors the
upstream it scrapes), so date formatting is explicit per endpoint:

============  ==============  ================================
Endpoint      Format          Helper
============  ==============  ================================
schedules     ``DD-MM-YYYY``  :func:`format_schedule_date`
tickets       ``YYYY-MM-DD``  :func:`format_ticket_date`
history       ISO 8601        :func:`format_history_datetime`
============  ==============  ================================
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any

from ._types import BBoxLike, DateLike, NotGiven

__all__ = [
    "build_query",
    "format_bbox",
    "format_history_datetime",
    "format_query_value",
    "format_schedule_date",
    "format_ticket_date",
]


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        # Must precede the int branch — bool is a subclass of int.
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, Enum):
        return _format_scalar(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return str(value)


def format_bbox(value: BBoxLike) -> str:
    """Serialise a bounding box to the wire format ``"lat1,lon1,lat2,lon2"`` (SW → NE).

    Strings are passed through so callers can hand over a pre-built bbox.
    """

    if isinstance(value, str):
        return value
    parts = list(value)
    if len(parts) != 4:
        raise ValueError(f"bbox must have 4 values (lat1, lon1, lat2, lon2), got {len(parts)}")
    return ",".join(_format_scalar(part) for part in parts)


def _coerce_date(value: DateLike) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def format_schedule_date(value: DateLike) -> str:
    """``/schedules/*`` wants ``DD-MM-YYYY``.

    ``date``/``datetime`` are converted; an ISO string is reformatted; any other
    string is passed through untouched (assumed already in the target format).
    """

    parsed = _coerce_date(value)
    if parsed is None:
        return str(value)
    return parsed.strftime("%d-%m-%Y")


def format_ticket_date(value: DateLike) -> str:
    """``/tickets/search`` wants ``YYYY-MM-DD``."""

    parsed = _coerce_date(value)
    if parsed is None:
        return str(value)
    return parsed.isoformat()


def format_history_datetime(value: DateLike) -> str:
    """``/{plan}/history/*`` wants ISO 8601 for ``start``/``end``.

    Naive datetimes are sent as-is (the API treats them as UTC); strings pass through.
    """

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def format_query_value(value: Any) -> str | None:
    """Serialise a single query value; ``None`` means "drop this parameter"."""

    if value is None or isinstance(value, NotGiven):
        return None
    if isinstance(value, (str, bytes)):
        return value.decode() if isinstance(value, bytes) else value
    if isinstance(value, Mapping):
        raise TypeError("mapping values are not supported in query parameters")
    if isinstance(value, Iterable):
        items = [format_query_value(item) for item in value]
        kept = [item for item in items if item is not None]
        if not kept:
            return None
        return ",".join(kept)
    return _format_scalar(value)


def build_query(*sources: Mapping[str, Any] | None) -> dict[str, str]:
    """Merge and serialise query params.

    * ``None`` and ``NOT_GIVEN`` values are dropped (never sent as empty strings).
    * ``bool`` becomes ``"true"``/``"false"`` — FastAPI rejects ``True``/``False``.
    * sequences (bbox tuples, ``exclude_qcode`` lists) become comma separated.
    * ``date``/``datetime`` fall back to ISO 8601; endpoint specific formats must be
      applied by the resource builder before calling this.
    """

    merged: dict[str, str] = {}
    for source in sources:
        if not source:
            continue
        for key, raw in source.items():
            serialised = format_query_value(raw)
            if serialised is None:
                merged.pop(key, None)
                continue
            merged[key] = serialised
    return merged
