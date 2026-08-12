"""Base class every SkyLink response model derives from."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = ["SkyLinkModel"]


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
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)
