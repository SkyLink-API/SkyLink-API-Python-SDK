"""Opt-in response caching: the store contract, a TTL store, and the key/TTL rules.

Nothing here runs unless a cache is handed to the client::

    from skylink_api import SkyLink
    from skylink_api.helpers.cache import MemoryCache

    cache = MemoryCache(ttls={"weather.metar": 60, "geo.*": 86_400})
    with SkyLink(api_key="...", cache=cache) as sky:
        sky.weather.metar("KJFK")   # network
        sky.weather.metar("KJFK")   # cache

Design notes, all of them deliberate:

* **Only GET, only success.** Nothing else may be replayed, and an error must
  never be remembered — a 429 that expired ten seconds ago is not an answer.
* **Monotonic time.** Expiry uses :func:`time.monotonic`, so a laptop waking up
  or an NTP step cannot resurrect an expired entry (or freeze a live one).
* **TTL is per operation, not per URL.** ``"weather.metar"`` covers every
  station; ``"weather.*"`` covers the namespace; ``default_ttl`` covers the
  rest. ``0`` means "do not cache", and it is the default — a bare
  ``MemoryCache()`` changes no behaviour at all.
* **The SDK caches the raw payload, not the parsed model.** See
  :class:`CacheProtocol` for why.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "CacheProtocol",
    "MemoryCache",
    "cache_key",
    "operation_name",
    "resolve_ttl",
]

#: How many entries :class:`MemoryCache` keeps before evicting the
#: least-recently-used one. A long-lived process must not grow without bound.
DEFAULT_MAX_ENTRIES = 512


@runtime_checkable
class CacheProtocol(Protocol):
    """What ``SkyLink(cache=...)`` needs: a ``get`` and a ``set``.

    Two methods, so a Redis/memcached/diskcache wrapper is a dozen lines::

        class RedisCache:
            def get(self, key): ...
            def set(self, key, value, ttl): ...

    ``value`` is a small JSON-shaped ``dict`` (``{"kind": ..., "payload": ...}``)
    holding the **decoded but not yet validated** response body, which is why an
    off-process store can serialise it. Caching the raw body rather than the
    parsed pydantic model is also what keeps a cache hit safe: every hit
    re-validates, so a caller who mutates a returned model cannot corrupt what
    the next caller gets.

    ``get`` returns ``None`` for a miss. ``ttl`` is in seconds; the SDK never
    calls ``set`` with a TTL it resolved to ``0`` (that means "do not cache"), so
    a store only ever sees a positive TTL — unless it opts out of TTL resolution
    entirely by not implementing :meth:`MemoryCache.ttl_for`, in which case it
    receives ``0.0`` and is expected to apply its own expiry policy.

    Exceptions raised by a cache are swallowed (with a warning): a broken cache
    degrades to no cache, it never fails a request.
    """

    def get(self, key: str) -> Any | None:
        """Cached value for ``key``, or ``None`` when absent or expired."""
        ...

    def set(self, key: str, value: Any, ttl: float) -> None:
        """Store ``value`` under ``key`` for ``ttl`` seconds."""
        ...


# ── keys and operation names ─────────────────────────────────────────────────


def cache_key(
    method: str,
    path: str,
    query: Mapping[str, str] | None = None,
    *,
    provider: str = "",
    base_url: str = "",
) -> str:
    """Build the cache key for one request.

    The key is ``provider | base_url | METHOD path?sorted-query``. Sorting the
    query makes ``?a=1&b=2`` and ``?b=2&a=1`` the same entry; provider and base
    URL are in the key because the same path on RapidAPI, on ``direct`` and on a
    staging instance are three different resources (and often three different
    datasets).

    Per-call headers are **not** part of the key — they carry auth and tracing,
    not identity.
    """

    pairs = sorted((str(name), str(value)) for name, value in (query or {}).items())
    encoded = "&".join(f"{name}={value}" for name, value in pairs)
    return f"{provider}|{base_url}|{method.upper()} {path}?{encoded}"


#: A path segment that is part of the *route* rather than a value substituted
#: into it. Endpoint segments are lower-case (``search``, ``winds-aloft``,
#: ``icao24``); the values callers pass are not (``KJFK``, ``G-STBA``,
#: ``4ca7b3``, ``BA123``).
_ROUTE_SEGMENT = re.compile(r"[a-z][a-z0-9_-]*\Z")

#: Namespaces whose first path segment does not spell the namespace out.
_NAMESPACE_PREFIXES = {"countries": "geo", "regions": "geo"}

#: History URLs start with the plan (``/ultra/history/...``); the plan is a
#: subscription detail, not a different operation.
_HISTORY_PLANS = frozenset({"ultra", "mega"})


def operation_name(path: str) -> str:
    """Derive the dotted operation name a TTL is looked up by.

    ``/weather/metar/KJFK`` → ``"weather.metar"``, ``/airports/search/text`` →
    ``"airports.search.text"``, ``/ultra/history/flights`` →
    ``"history.flights"``, ``/countries/US`` → ``"geo.countries"``.

    The rule is cheap on purpose: take the leading path segments that look like
    route segments (lower-case, not starting with a digit) and stop at the first
    one that looks like a caller-supplied value. Two touch-ups keep the names in
    line with the namespaces they belong to: the ``/{plan}/history`` prefix loses
    its plan, and ``/countries``/``/regions`` gain their ``geo.`` namespace.

    A caller who passes a *lower-case* code (``/weather/metar/kjfk``) gets the
    longer ``"weather.metar.kjfk"``; :func:`resolve_ttl` walks the name from the
    longest prefix down, so the ``"weather.metar"`` rule still applies. Set
    :attr:`~skylink_api._types.RequestSpec.operation` to bypass the heuristic
    entirely.
    """

    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return ""
    if segments[0] in _HISTORY_PLANS and len(segments) > 1 and segments[1] == "history":
        segments = segments[1:]

    parts: list[str] = []
    for index, segment in enumerate(segments):
        if index == 0:
            prefix = _NAMESPACE_PREFIXES.get(segment)
            if prefix is not None:
                parts.append(prefix)
            parts.append(segment)
            continue
        if not _ROUTE_SEGMENT.match(segment):
            break
        parts.append(segment)
    return ".".join(parts)


def resolve_ttl(operation: str, ttls: Mapping[str, float], default_ttl: float) -> float:
    """TTL in seconds for ``operation``: exact name, then prefixes, then default.

    ``"weather.metar"`` is tried first, then ``"weather.metar.*"``, then
    ``"weather"``, ``"weather.*"`` and finally ``"*"`` before falling back to
    ``default_ttl``. Longest match wins, so a namespace-wide rule can be
    overridden for a single operation::

        ttls={"geo.*": 86_400, "geo.regions": 300}
    """

    parts = operation.split(".") if operation else []
    for length in range(len(parts), 0, -1):
        prefix = ".".join(parts[:length])
        if prefix in ttls:
            return float(ttls[prefix])
        wildcard = f"{prefix}.*"
        if wildcard in ttls:
            return float(ttls[wildcard])
    if "*" in ttls:
        return float(ttls["*"])
    return float(default_ttl)


# ── the store ────────────────────────────────────────────────────────────────


class MemoryCache:
    """In-process TTL cache with LRU eviction — the default :class:`CacheProtocol`.

    Args:
        default_ttl: Seconds to keep an operation that no rule matches. ``0``
            (the default) means "do not cache", so ``MemoryCache()`` on its own
            is inert — caching is always something you asked for.
        ttls: Per-operation seconds, by exact name (``"weather.metar"``) or by
            prefix (``"weather.*"``, ``"*"``). See :func:`resolve_ttl`.
        max_entries: Cap on stored entries; the least recently used one is
            evicted past it, so a long-running process cannot leak.
        monotonic: Clock used for expiry — :func:`time.monotonic`, never
            :func:`time.time`, so that changing the system clock cannot break
            TTLs. Injectable for tests.

    The instance is thread-safe (``client.batch`` fans out over threads).
    """

    def __init__(
        self,
        *,
        default_ttl: float = 0.0,
        ttls: Mapping[str, float] | None = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError(f"max_entries must be >= 1, got {max_entries}")
        self.default_ttl = float(default_ttl)
        self.ttls: dict[str, float] = {key: float(value) for key, value in (ttls or {}).items()}
        self.max_entries = max_entries
        self._monotonic = monotonic
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    # ── CacheProtocol ────────────────────────────────────────────────────────

    def get(self, key: str) -> Any | None:
        """Value for ``key``, or ``None`` when it is missing or expired."""

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= self._monotonic():
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        """Store ``value`` for ``ttl`` seconds; a non-positive ``ttl`` stores nothing."""

        if ttl <= 0:
            return
        with self._lock:
            self._entries[key] = (self._monotonic() + float(ttl), value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    # ── TTL policy ───────────────────────────────────────────────────────────

    def ttl_for(self, operation: str) -> float:
        """Seconds this cache wants ``operation`` kept for; ``0`` disables caching.

        The client calls this before every cacheable request, which is how a
        custom store can implement its own policy: define ``ttl_for`` and the SDK
        will honour it, omit it and the SDK caches every successful GET with
        ``ttl=0.0``, leaving expiry entirely to the store.
        """

        return resolve_ttl(operation, self.ttls, self.default_ttl)

    # ── housekeeping ─────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Drop every entry."""

        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        """Number of stored entries, expired ones included (they die on read)."""

        with self._lock:
            return len(self._entries)

    def __repr__(self) -> str:
        return (
            f"MemoryCache(entries={len(self)}, max_entries={self.max_entries}, "
            f"default_ttl={self.default_ttl}, rules={len(self.ttls)})"
        )
