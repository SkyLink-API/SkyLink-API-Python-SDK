"""Results produced by the pollers (``sky.poll``).

Unlike everything else in :mod:`skylink_api.models`, :class:`AdsbDiff` is not a
payload the API sends: it is computed by the SDK from two consecutive
``GET /adsb/aircraft`` snapshots. Hence a plain dataclass rather than a
:class:`~skylink_api.models._base.SkyLinkModel` — there is nothing to parse and
no unknown backend fields to keep.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .adsb import AdsbAircraft

__all__ = ["AdsbDiff"]


@dataclass(frozen=True)
class AdsbDiff:
    """What changed in the live feed between two polls.

    Yielded by :meth:`skylink_api.resources.poll.Poll.adsb`, one per poll. The
    comparison key is ``icao24`` — the only identifier the feed always carries —
    **lower-cased**, because responses upper-case it while queries take it lower
    case, and a live map keyed on the raw value would double-count an aircraft
    after a backend change. Aircraft broadcasting no ``icao24`` at all cannot be
    tracked across polls and are dropped from every field here.

    Attributes:
        appeared: Aircraft present now and absent from the previous snapshot. On
            the first poll this is the whole feed (see ``is_first``).
        disappeared: ``icao24`` values (lower case) that were in the previous
            snapshot and are gone now. Strings, not objects: the aircraft is no
            longer in the payload, so there is nothing to hand back. "Gone"
            usually means "out of receiver range", not "landed".
        updated: Aircraft present in both snapshots whose **position, altitude
            or ground speed** changed. Aircraft that merely re-sent the same
            values (a parked airframe, a repeated message) are not listed.
        snapshot: The full current feed, ``{icao24: aircraft}`` — the state
            ``appeared``/``disappeared``/``updated`` describe the transition to.
            Use it as the map's source of truth; the three lists are the delta
            to animate.
        is_first: ``True`` for the very first poll, where there is no previous
            snapshot to compare against and everything is reported as
            ``appeared``. Check it before drawing "new aircraft" notifications
            for what is really just the initial load.
    """

    appeared: list[AdsbAircraft] = field(default_factory=list)
    disappeared: list[str] = field(default_factory=list)
    updated: list[AdsbAircraft] = field(default_factory=list)
    snapshot: dict[str, AdsbAircraft] = field(default_factory=dict)
    is_first: bool = False
