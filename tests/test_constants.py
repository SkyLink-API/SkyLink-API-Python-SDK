"""Enumerable constants (contract §10) and the ``WebhookEvent`` enum.

The constants are the runtime twins of the ``Literal`` types on the resources.
Every test here exists to catch the one failure mode that matters: a literal and
its tuple drifting apart, so that a dropdown built from the tuple offers a value
the type checker rejects (or vice versa).
"""

from __future__ import annotations

import json
from typing import Any, get_args

import pytest

import skylink_api
from skylink_api._constants import (
    CHART_CATEGORIES,
    CONTINENTS,
    FLIGHT_CATEGORIES,
    HISTORY_PLANS,
    WEBHOOK_EVENTS,
)
from skylink_api._types import HistoryPlan
from skylink_api.helpers.weather import FlightCategory
from skylink_api.models.webhooks import WebhookEvent
from skylink_api.resources.charts import ChartCategory
from skylink_api.resources.geo import Continent
from skylink_api.resources.webhooks import WebhookEventType

ALL_CONSTANTS: dict[str, tuple[str, ...]] = {
    "CHART_CATEGORIES": CHART_CATEGORIES,
    "WEBHOOK_EVENTS": WEBHOOK_EVENTS,
    "CONTINENTS": CONTINENTS,
    "HISTORY_PLANS": HISTORY_PLANS,
    "FLIGHT_CATEGORIES": FLIGHT_CATEGORIES,
}


# ── shape ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(ALL_CONSTANTS))
def test_every_constant_is_a_non_empty_tuple_of_unique_strings(name: str) -> None:
    values = ALL_CONSTANTS[name]
    assert isinstance(values, tuple)
    assert values
    assert all(isinstance(value, str) and value for value in values)
    assert len(values) == len(set(values))


@pytest.mark.parametrize("name", sorted(ALL_CONSTANTS))
def test_every_constant_is_exported_from_the_package_root(name: str) -> None:
    assert name in skylink_api.__all__
    assert getattr(skylink_api, name) is ALL_CONSTANTS[name]


# ── agreement with the literal types ─────────────────────────────────────────


def test_chart_categories_match_the_literal() -> None:
    """``models/v3/charts.py:ChartCategory`` on the backend: GEN GND SID STAR APP."""

    assert get_args(ChartCategory) == CHART_CATEGORIES
    assert CHART_CATEGORIES == ("GEN", "GND", "SID", "STAR", "APP")


def test_webhook_events_match_the_literal_and_the_enum() -> None:
    """``VALID_EVENTS`` in ``services/v31/webhook_service.py`` — exactly six."""

    assert set(WEBHOOK_EVENTS) == set(get_args(WebhookEventType))
    assert set(WEBHOOK_EVENTS) == {event.value for event in WebhookEvent}
    assert len(WEBHOOK_EVENTS) == 6


def test_continents_match_the_literal() -> None:
    """``_VALID_CONTINENTS`` in ``routers/v3/countries.py``.

    ``"NA"`` was listed for a while because the API accepted it rather than
    because it worked — pandas read the literal ``NA`` as NaN and the filter
    matched nothing. Fixed backend side (verified 2026-08-15): all seven codes
    resolve, and ``compose.north_america_countries()`` is a convenience now, not
    a workaround.
    """

    assert get_args(Continent) == CONTINENTS
    assert CONTINENTS == ("AF", "AN", "AS", "EU", "NA", "OC", "SA")
    assert "NA" in CONTINENTS


def test_history_plans_match_the_literal() -> None:
    assert get_args(HistoryPlan) == HISTORY_PLANS


def test_flight_categories_match_the_helper_literal() -> None:
    """Derived by the SDK, not sent by the API — worst-to-best order is ours."""

    assert set(FLIGHT_CATEGORIES) == set(get_args(FlightCategory))
    assert FLIGHT_CATEGORIES[0] == "VFR"


# ── WebhookEvent ─────────────────────────────────────────────────────────────


def test_webhook_event_is_a_string_everywhere_a_string_is_expected() -> None:
    assert WebhookEvent.STATUS_CHANGED == "status_changed"
    assert isinstance(WebhookEvent.GATE_CHANGED, str)
    assert str(WebhookEvent.GATE_CHANGED) == "gate_changed"
    assert f"{WebhookEvent.FLIGHT_LANDED}" == "flight_landed"
    assert "status_changed" in list(WebhookEvent)


def test_webhook_event_serialises_to_the_wire_value() -> None:
    body: dict[str, Any] = {"event_types": [WebhookEvent.FLIGHT_DELAYED]}
    assert json.loads(json.dumps(body)) == {"event_types": ["flight_delayed"]}


def test_webhook_event_can_be_built_from_the_wire_value() -> None:
    assert WebhookEvent("gate_changed") is WebhookEvent.GATE_CHANGED
    with pytest.raises(ValueError, match="not a valid"):
        WebhookEvent("flight_taxiing")


def test_webhook_event_is_exported_from_the_root() -> None:
    assert skylink_api.WebhookEvent is WebhookEvent
    assert "WebhookEvent" in skylink_api.__all__
