"""Pure helpers around the SkyLink API — no network, no client, no dependencies.

Everything here is a plain function over values you already hold, so it is free
to call, trivial to test and safe to use outside a client context::

    from skylink_api import helpers

    box = helpers.bbox_around(40.64, -73.78, radius_km=50)
    live = sky.adsb.aircraft(bbox=box)
    layer = helpers.geojson.adsb_to_geojson(live)

The modules:

=================  ===========================================================
:mod:`units`       ft/m, kt/km-h, inHg/hPa … plus the parsers for the values
                   the API sends without a unit (``normalize_altimeter``,
                   ``parse_visibility``, ``humidity_to_percent``).
:mod:`spatial`     bounding-box strings, great-circle distance and bearing,
                   track statistics and simplification.
:mod:`idents`      IATA/ICAO/pseudo-code classification, ICAO24 and
                   registration normalisation, flight-number splitting.
:mod:`weather`     flight category, ceiling, report age, crosswind components.
:mod:`geojson`     RFC 7946 exporters for ADS-B, tracks, airports and navaids.
:mod:`sentinels`   turn the HTTP-200 "not found" payloads into exceptions.
:mod:`batch`       bounded-concurrency fan-out plus the readers for what
                   ``client.batch`` returns (``successes``, ``failures``,
                   ``raise_for_errors``).
:mod:`cache`       the opt-in response cache (``MemoryCache``, ``CacheProtocol``)
                   handed to ``SkyLink(cache=...)``.
=================  ===========================================================

The modules are the public surface; the handful of names re-exported here are
just the ones that come up in nearly every script.
"""

from __future__ import annotations

from . import batch, cache, geojson, idents, sentinels, spatial, units, weather
from .batch import failures, successes
from .cache import CacheProtocol, MemoryCache
from .spatial import bbox, bbox_around
from .units import normalize_altimeter
from .weather import flight_category

__all__ = [
    "CacheProtocol",
    "MemoryCache",
    "batch",
    "bbox",
    "bbox_around",
    "cache",
    "failures",
    "flight_category",
    "geojson",
    "idents",
    "normalize_altimeter",
    "sentinels",
    "spatial",
    "successes",
    "units",
    "weather",
]
