"""One function that turns a list-shaped SkyLink response into a DataFrame.

pandas is an **optional** dependency — install it with the extra::

    pip install "skylink-api[pandas]"

and then::

    from skylink_api import SkyLink
    from skylink_api.pandas_ext import to_dataframe

    with SkyLink(api_key="...") as sky:
        frame = to_dataframe(sky.adsb.aircraft(bbox="51,-1,52,0.5"))
        frame = to_dataframe(sky.navaids.search(country="GB", limit=500))

Why a free function and not a ``.to_dataframe()`` method on the models: a method
would put ``pandas.DataFrame`` in the SDK's own type annotations, which means
every user — including the 95% who never touch pandas — would need it installed
for ``mypy`` to type-check their code. Keeping the conversion outside the models
keeps the core dependency-free; the import happens the first time you call this
function and nowhere else.

Three things worth knowing about the frames that come out:

* **Rows are the API's rows.** Nothing is renamed, reordered or recomputed, and
  ``extra="allow"`` means a column can appear or vanish between deployments —
  the payloads are scraped. Select the columns you rely on rather than assuming
  a fixed frame.
* **Numeric-looking strings stay strings.** The API sends ``frequency_mhz:
  "119.1"``, ``active: "Y"``, ``year_built: "2019"``, and the SDK does not
  coerce them, so those columns arrive with dtype ``object``. Convert
  explicitly (``pd.to_numeric(frame["frequency_mhz"], errors="coerce")``) —
  guessing that at the SDK level is how ``"Y"`` becomes ``NaN`` silently.
* **Nested objects stay nested.** A column can hold dicts (a ticket's ``legs``,
  a METAR's ``parsed``). ``pandas.json_normalize(frame["parsed"])`` or
  :func:`to_dataframe` on the inner list is the way out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover - import only for annotations
    import pandas

__all__ = ["LIST_FIELDS", "to_dataframe"]

#: Response fields :func:`to_dataframe` unwraps, in priority order.
#:
#: Every list-shaped endpoint of the API wraps its rows in an envelope with
#: counts and echoed filters next to them (``{"count": 812, "aircraft": [...]}``),
#: and the field holding the rows is named after them. These are those names.
#:
#: The order matters for the one response that carries two row lists:
#: ``history.positions()`` returns ``positions`` **and** the ``flights`` they
#: belong to, and the positions are what a frame is usually wanted for. Pass
#: ``field="flights"`` for the other one.
#:
#: A field that is present but **empty** loses to a later field that has rows,
#: so an ``{positions: [], flights: [...]}`` answer still gives a usable frame;
#: when every candidate is empty the result is an empty frame.
LIST_FIELDS: tuple[str, ...] = (
    "aircraft",  # adsb.aircraft()
    "positions",  # history.track(), history.positions()
    "flights",  # history.flights(), schedules.*, tickets.search()
    "navaids",  # navaids.search()
    "countries",  # geo.countries()
    "regions",  # geo.regions()
    "airports",  # airports.search_location(), search_ip(), search_text()
    "notams",  # notams.by_airport()
    "reports",  # weather.pireps(), weather.airsigmets()
    "stations",  # weather.winds_aloft()
    "routes",  # routes.by_airport(), routes.pairs()
    "sources",  # charts.sources()
)


def _require_pandas() -> Any:
    """Import pandas on first use, or explain how to get it.

    Deferred on purpose: importing this module must stay free for a caller who
    only wants to read :data:`LIST_FIELDS`, and pandas costs ~0.5 s to import.
    """

    try:
        import pandas
    except ImportError as err:  # pragma: no cover - exercised only without pandas
        raise ImportError(
            "skylink_api.pandas_ext needs pandas, which is an optional dependency — "
            'install it with: pip install "skylink-api[pandas]"'
        ) from err
    return pandas


def _is_record(item: object) -> bool:
    """Whether one item can become a DataFrame row (a model or a mapping)."""

    return isinstance(item, BaseModel | Mapping)


def _rows(value: object) -> list[dict[str, Any]] | None:
    """Turn a candidate value into row dicts, or ``None`` when it is not rows.

    Any iterable of records works — a list, a generator, ``snapshot.values()``
    from a poller diff — except a mapping, which is treated as an envelope to
    look inside rather than as a sequence of its keys.

    A list of scalars is deliberately rejected (``None``, not a one-column
    frame): ``VrsRouteResult.airports`` is a list of ICAO **strings**, and
    guessing it into a frame would hide the fact that the object has no rows at
    all. Mixed lists are rejected for the same reason.
    """

    if isinstance(value, str | bytes | Mapping | BaseModel):
        return None
    if not isinstance(value, Iterable):
        return None
    items = list(value)
    if not all(_is_record(item) for item in items):
        return None
    return [
        item.model_dump(mode="python") if isinstance(item, BaseModel) else dict(item)
        for item in items
    ]


def _get(source: object, name: str) -> object:
    """Read ``name`` off a mapping or a model, ``None`` when it is absent."""

    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _has(source: object, name: str) -> bool:
    if isinstance(source, Mapping):
        return name in source
    return hasattr(source, name)


def _unwrap(obj: object) -> list[dict[str, Any]]:
    """Find the rows of ``obj``, or raise a message that says what was tried."""

    rows = _rows(obj)
    if rows is not None:
        return rows

    if isinstance(obj, BaseModel | Mapping):
        fallback: list[dict[str, Any]] | None = None
        for name in LIST_FIELDS:
            if not _has(obj, name):
                continue
            candidate = _rows(_get(obj, name))
            if candidate:
                return candidate
            if candidate is not None and fallback is None:
                fallback = candidate  # an empty list is a valid, empty frame
        if fallback is not None:
            return fallback

    raise TypeError(
        f"to_dataframe() cannot find rows in {type(obj).__name__}. It accepts a list of "
        "models or dicts, or a response whose rows live in one of: "
        f"{', '.join(LIST_FIELDS)}. Pass field='<name>' to point at another list."
    )


def to_dataframe(obj: object, *, field: str | None = None) -> pandas.DataFrame:
    """Build a :class:`pandas.DataFrame` from a list-shaped SkyLink response.

    One row per API row, one column per field, no reshaping::

        frame = to_dataframe(sky.adsb.aircraft(bbox="51,-1,52,0.5"))
        frame = to_dataframe(sky.schedules.departures("EGLL"))
        frame = to_dataframe(sky.history.track("4ca7b3"))       # positions
        frame = to_dataframe(sky.geo.countries())
        frame = to_dataframe(page.aircraft)                     # a bare list works too

    Args:
        obj: A response envelope whose rows sit in one of :data:`LIST_FIELDS`
            (``adsb.aircraft()``, both schedule boards, ``history`` flights and
            positions, ``navaids``, ``countries``, ``regions``, ``tickets`` and
            the rest), a bare list of models, or a bare list of dicts — the raw
            payload of ``client.request()`` included.
        field: Take the rows from this field instead of guessing. Needed for the
            responses that carry two lists, e.g.
            ``to_dataframe(sky.history.positions(...), field="flights")``.

    Returns:
        A frame with the rows in API order and a default ``RangeIndex``. An
        empty response gives an **empty frame with no columns**, so guard with
        ``frame.empty`` before selecting a column.

    Raises:
        ImportError: pandas is not installed — ``pip install "skylink-api[pandas]"``.
        TypeError: no list of records was found; the message lists the fields
            that were tried.
        KeyError: ``field`` does not exist on ``obj``.
        ValueError: ``field`` exists but does not hold a list of records.

    Note:
        Values are **not** converted: string numbers stay strings, opaque time
        strings (flight status, NOTAM validity, FAA delay durations) stay
        strings, and unknown backend fields become columns of their own. Only
        the values the models already parse (timestamps, floats) arrive typed.
    """

    pandas = _require_pandas()

    if field is not None:
        if not _has(obj, field):
            raise KeyError(f"{type(obj).__name__} has no field {field!r}")
        rows = _rows(_get(obj, field))
        if rows is None:
            raise ValueError(
                f"field {field!r} of {type(obj).__name__} is not a list of models or dicts"
            )
    else:
        rows = _unwrap(obj)

    return pandas.DataFrame(rows)
