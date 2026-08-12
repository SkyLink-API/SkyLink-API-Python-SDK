"""Type aliases shared by more than one domain model."""

from __future__ import annotations

from typing import TypeAlias

__all__ = ["Number"]

#: A numeric wire value that may arrive as an ``int`` or a ``float``.
#:
#: Annotating such fields as plain ``float`` would make pydantic coerce whole
#: numbers (a wind direction of ``270``, an altitude of ``3000``) into floats;
#: the union keeps whichever type the API actually sent.
Number: TypeAlias = int | float
