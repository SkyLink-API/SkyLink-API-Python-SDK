"""Base class every SkyLink response model derives from."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

__all__ = ["SkyLinkModel"]

#: Strings longer than this are cut short in ``repr`` — a raw METAR is 200+
#: characters and a repr is meant to be read, not parsed.
_REPR_MAX_STRING = 48


def _format_value(value: Any) -> str:
    if isinstance(value, str) and len(value) > _REPR_MAX_STRING:
        return repr(f"{value[: _REPR_MAX_STRING - 1]}…")
    return repr(value)


class SkyLinkModel(BaseModel):
    """Permissive pydantic base for API responses.

    Two deliberate settings:

    * ``extra="allow"`` — the API serves scraped data, so fields appear and
      disappear between deployments. Unknown keys are kept on the instance (and
      round-trip through :meth:`model_dump`) instead of raising, which means a
      backend addition never breaks a pinned SDK version.
    * ``populate_by_name=True`` — models that alias wire names (``usageType``,
      the PascalCase schedule rows, ``from``/``to``) can still be constructed
      from their Python attribute names in tests and user code.

    Fields are optional by default across the SDK for the same reason: a missing
    key is far more common than a wrong one.

    Models that set :attr:`__repr_fields__` get a short ``repr`` built from those
    fields instead of pydantic's full dump — with ``extra="allow"`` and scraped
    payloads the default one runs to hundreds of characters, which is unreadable
    in a REPL, a log line or a list comprehension's output.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    #: Fields shown by :meth:`__repr__`, in order. Empty (the default) keeps
    #: pydantic's exhaustive repr, so this is opt-in per model.
    __repr_fields__: ClassVar[tuple[str, ...]] = ()

    def __repr__(self) -> str:
        """``Metar(icao='KJFK', timestamp=..., raw='KJFK 271851Z …')``.

        Fields that are ``None`` or empty are dropped, long strings are cut at
        48 characters. Models without :attr:`__repr_fields__` keep pydantic's
        default repr.
        """

        names = type(self).__repr_fields__
        if not names:
            return super().__repr__()
        parts = [
            f"{name}={_format_value(value)}"
            for name in names
            if (value := getattr(self, name, None)) is not None and value != []
        ]
        return f"{type(self).__name__}({', '.join(parts)})"
