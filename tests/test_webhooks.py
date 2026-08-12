"""``sky.webhooks`` — the only namespace with POST/PATCH/DELETE.

Fixtures are hand-built from ``services/v31/webhook_service.py`` (see
``fixtures/SOURCES.md``): ``webhook_created.json`` is the ``201`` body,
``webhooks_list.json`` the two-field-wider list rows.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL, load_fixture
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import (
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableEntityError,
)
from skylink_api.models.webhooks import (
    Webhook,
    WebhookEvent,
    WebhookSubscription,
    WebhookToggleResponse,
)
from skylink_api.resources.webhooks import (
    AsyncWebhooks,
    Webhooks,
    _create_spec,
    _delete_spec,
    _event_types_spec,
    _list_spec,
    _update_spec,
)

HOOK_ID = "0a4f9c2e-7b31-4d85-9f60-31c8a2b7e410"
HOOK_URL = "https://hooks.example.com/skylink"

#: The six events the backend accepts (``VALID_EVENTS``, sorted as it sorts them).
ALL_EVENT_TYPES = [
    "flight_boarding",
    "flight_cancelled",
    "flight_delayed",
    "flight_landed",
    "gate_changed",
    "status_changed",
]


@pytest.fixture
def webhooks(client: SkyLink) -> Webhooks:
    """The namespace, built directly — wiring onto the client is task A8."""

    return Webhooks(client)


@pytest.fixture
def async_webhooks(async_client: AsyncSkyLink) -> AsyncWebhooks:
    return AsyncWebhooks(async_client)


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builders_produce_the_documented_specs() -> None:
    create = _create_spec(
        url=HOOK_URL, event_types=["status_changed"], filters={"flight_number": "BA117"}
    )
    assert create.method == "POST"
    assert create.path == "/webhooks"
    assert create.json_body == {
        "url": HOOK_URL,
        "event_types": ["status_changed"],
        "filters": {"flight_number": "BA117"},
    }
    assert create.cast_to is Webhook

    # filters is never sent as null — the backend field is a dict with a default.
    assert _create_spec(url=HOOK_URL, event_types=["gate_changed"]).json_body["filters"] == {}

    assert _list_spec().method == "GET"
    assert _list_spec().path == "/webhooks"

    update = _update_spec(HOOK_ID, active=False)
    assert update.method == "PATCH"
    assert update.path == f"/webhooks/{HOOK_ID}"
    assert update.json_body == {"active": False}

    delete = _delete_spec(HOOK_ID)
    assert delete.method == "DELETE"
    assert delete.path == f"/webhooks/{HOOK_ID}"
    # 204 carries no body at all.
    assert delete.response_kind == "none"
    assert delete.cast_to is None

    assert _event_types_spec().path == "/webhooks/events"


# ── create ───────────────────────────────────────────────────────────────────


def test_create_posts_json_and_reads_a_201(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(201, json=load_fixture("webhook_created"))
    )

    hook = webhooks.create(
        url=HOOK_URL,
        event_types=["status_changed", "flight_delayed"],
        filters={"flight_number": "BA117"},
    )

    request = route.calls.last.request
    assert request.method == "POST"
    assert request.url.path == "/v3.1/webhooks"
    assert request.headers["x-api-key"] == "test-key"
    assert json.loads(request.content) == {
        "url": HOOK_URL,
        "event_types": ["status_changed", "flight_delayed"],
        "filters": {"flight_number": "BA117"},
    }

    assert isinstance(hook, Webhook)
    assert hook.id == HOOK_ID
    assert hook.url == HOOK_URL
    assert hook.event_types == ["status_changed", "flight_delayed"]
    assert hook.filters == {"flight_number": "BA117"}
    assert hook.active is True
    assert hook.created_at == datetime(2026, 2, 11, 12, 0, tzinfo=timezone.utc)
    # The 201 body is a strict subset of a list row — no delivery health yet.
    assert not hasattr(hook, "failure_count")


def test_create_without_filters_sends_an_empty_map(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(201, json=load_fixture("webhook_created"))
    )

    webhooks.create(url=HOOK_URL, event_types=["gate_changed"])

    assert json.loads(route.calls.last.request.content)["filters"] == {}


def test_create_rejected_on_a_plan_without_webhooks(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(
            403, json={"detail": "Webhook subscriptions require PRO, ULTRA, or MEGA plan"}
        )
    )

    with pytest.raises(PermissionDeniedError) as excinfo:
        webhooks.create(url=HOOK_URL, event_types=["status_changed"])

    assert excinfo.value.status_code == 403


def test_create_limit_reached_is_a_422(webhooks: Webhooks, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(
            422, json={"detail": "Webhook limit reached (3). Delete an existing webhook first."}
        )
    )

    with pytest.raises(UnprocessableEntityError):
        webhooks.create(
            url=HOOK_URL, event_types=["status_changed"], filters={"flight_number": "BA117"}
        )


def test_create_is_not_replayed_after_a_500(client: SkyLink, respx_mock: respx.MockRouter) -> None:
    """POST is unsafe: a 5xx must not be retried, or a duplicate hook appears."""

    route = respx_mock.post(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )

    with pytest.raises(InternalServerError):
        Webhooks(client).create(url=HOOK_URL, event_types=["status_changed"])

    assert route.call_count == 1


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_unwraps_the_envelope(webhooks: Webhooks, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(200, json=load_fixture("webhooks_list"))
    )

    result = webhooks.list()

    assert route.calls.last.request.url.path == "/v3.1/webhooks"
    # The wire body is {count, webhooks[]}; the SDK hands back the list itself.
    assert isinstance(result, list)
    assert len(result) == 2
    first, second = result
    assert isinstance(first, WebhookSubscription)

    # List rows are two fields wider than the create body.
    assert first.last_triggered_at == datetime(2026, 2, 11, 13, 5, 22, tzinfo=timezone.utc)
    assert first.failure_count == 0
    assert first.active is True

    # A hook the dispatcher disabled after 10 failed deliveries.
    assert second.active is False
    assert second.failure_count == 10
    assert second.last_triggered_at is None
    assert second.event_types == ["gate_changed"]


def test_list_empty(webhooks: Webhooks, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(200, json={"count": 0, "webhooks": []})
    )

    assert webhooks.list() == []


# ── update ───────────────────────────────────────────────────────────────────


def test_update_patches_and_returns_the_acknowledgement(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.patch(f"{TEST_BASE_URL}/webhooks/{HOOK_ID}").mock(
        return_value=httpx.Response(200, json={"id": HOOK_ID, "active": False})
    )

    result = webhooks.update(HOOK_ID, active=False)

    request = route.calls.last.request
    assert request.method == "PATCH"
    assert request.url.path == f"/v3.1/webhooks/{HOOK_ID}"
    assert json.loads(request.content) == {"active": False}

    # Two keys only — not the full subscription.
    assert isinstance(result, WebhookToggleResponse)
    assert result.id == HOOK_ID
    assert result.active is False
    assert result.model_dump() == {"id": HOOK_ID, "active": False}


def test_update_unknown_id_is_a_404(webhooks: Webhooks, respx_mock: respx.MockRouter) -> None:
    respx_mock.patch(f"{TEST_BASE_URL}/webhooks/{HOOK_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "Webhook not found or not owned by you"})
    )

    with pytest.raises(NotFoundError):
        webhooks.update(HOOK_ID, active=True)


# ── delete ───────────────────────────────────────────────────────────────────


def test_delete_returns_none_on_204(webhooks: Webhooks, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.delete(f"{TEST_BASE_URL}/webhooks/{HOOK_ID}").mock(
        return_value=httpx.Response(204)
    )

    result = webhooks.delete(HOOK_ID)

    assert route.calls.last.request.method == "DELETE"
    assert route.calls.last.request.url.path == f"/v3.1/webhooks/{HOOK_ID}"
    # 204 with an empty body: nothing is parsed, nothing is returned.
    assert result is None


def test_delete_unknown_id_is_a_404(webhooks: Webhooks, respx_mock: respx.MockRouter) -> None:
    respx_mock.delete(f"{TEST_BASE_URL}/webhooks/{HOOK_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "Webhook not found or not owned by you"})
    )

    with pytest.raises(NotFoundError):
        webhooks.delete(HOOK_ID)


def test_delete_bad_uuid_is_a_422(webhooks: Webhooks, respx_mock: respx.MockRouter) -> None:
    respx_mock.delete(f"{TEST_BASE_URL}/webhooks/not-a-uuid").mock(
        return_value=httpx.Response(422, json={"detail": "Invalid UUID"})
    )

    with pytest.raises(UnprocessableEntityError):
        webhooks.delete("not-a-uuid")


# ── event types ──────────────────────────────────────────────────────────────


def test_event_types_unwraps_and_matches_the_literal(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(f"{TEST_BASE_URL}/webhooks/events").mock(
        return_value=httpx.Response(200, json={"event_types": ALL_EVENT_TYPES})
    )

    result = webhooks.event_types()

    assert route.calls.last.request.url.path == "/v3.1/webhooks/events"
    assert result == ALL_EVENT_TYPES
    assert len(result) == 6


# ── round trip ───────────────────────────────────────────────────────────────


def test_create_list_update_delete_round_trip(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    created = load_fixture("webhook_created")
    listed = {
        "count": 1,
        "webhooks": [
            {
                **created,
                "last_triggered_at": None,
                "failure_count": 0,
            }
        ],
    }
    respx_mock.post(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(201, json=created)
    )
    respx_mock.get(f"{TEST_BASE_URL}/webhooks").mock(return_value=httpx.Response(200, json=listed))
    respx_mock.patch(f"{TEST_BASE_URL}/webhooks/{HOOK_ID}").mock(
        return_value=httpx.Response(200, json={"id": HOOK_ID, "active": False})
    )
    respx_mock.delete(f"{TEST_BASE_URL}/webhooks/{HOOK_ID}").mock(return_value=httpx.Response(204))

    hook = webhooks.create(
        url=HOOK_URL,
        event_types=["status_changed", "flight_delayed"],
        filters={"flight_number": "BA117"},
    )
    assert hook.id is not None

    rows = webhooks.list()
    assert [row.id for row in rows] == [hook.id]
    # The list row is a superset of what create returned.
    assert rows[0].url == hook.url
    assert rows[0].failure_count == 0

    toggled = webhooks.update(hook.id, active=False)
    assert toggled.active is False

    assert webhooks.delete(hook.id) is None


# ── ensure ───────────────────────────────────────────────────────────────────
#
# The fixture list already holds a subscription on HOOK_URL with
# ["status_changed", "flight_delayed"] and filters {"flight_number": "BA117"},
# active — that is the "already correct" state every case below reconciles
# against.

LIVE_EVENTS = ["status_changed", "flight_delayed"]
LIVE_FILTERS = {"flight_number": "BA117"}


class Routes:
    """The four webhook routes, mocked in one place so writes can be counted."""

    def __init__(self, respx_mock: respx.MockRouter, *, listed: object = None) -> None:
        self.list = respx_mock.get(f"{TEST_BASE_URL}/webhooks").mock(
            return_value=httpx.Response(
                200, json=listed if listed is not None else load_fixture("webhooks_list")
            )
        )
        self.create = respx_mock.post(f"{TEST_BASE_URL}/webhooks").mock(
            return_value=httpx.Response(201, json=load_fixture("webhook_created"))
        )
        self.update = respx_mock.patch(url__startswith=f"{TEST_BASE_URL}/webhooks/").mock(
            return_value=httpx.Response(200, json={"id": HOOK_ID, "active": False})
        )
        self.delete = respx_mock.delete(url__startswith=f"{TEST_BASE_URL}/webhooks/").mock(
            return_value=httpx.Response(204)
        )

    @property
    def writes(self) -> tuple[int, int, int]:
        """``(created, patched, deleted)`` — the whole point of ``ensure``."""

        return (self.create.call_count, self.update.call_count, self.delete.call_count)


def test_ensure_is_a_no_op_when_the_subscription_already_matches(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    routes = Routes(respx_mock)

    hook = webhooks.ensure(HOOK_URL, LIVE_EVENTS, filters=LIVE_FILTERS)

    assert routes.writes == (0, 0, 0)
    assert routes.list.call_count == 1
    # The existing row comes back, delivery health and all.
    assert isinstance(hook, WebhookSubscription)
    assert hook.id == HOOK_ID
    assert hook.failure_count == 0


def test_ensure_ignores_event_order_and_flight_number_case(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    """A subscription is a *set* of events, and the backend upper-cases the number."""

    routes = Routes(respx_mock)

    webhooks.ensure(
        f"  {HOOK_URL} ",
        ["flight_delayed", "status_changed"],
        filters={"flight_number": "ba117"},
    )

    assert routes.writes == (0, 0, 0)


def test_ensure_accepts_the_event_enum(webhooks: Webhooks, respx_mock: respx.MockRouter) -> None:
    routes = Routes(respx_mock)

    webhooks.ensure(
        HOOK_URL,
        [WebhookEvent.STATUS_CHANGED, WebhookEvent.FLIGHT_DELAYED],
        filters=LIVE_FILTERS,
    )

    assert routes.writes == (0, 0, 0)


def test_ensure_without_filters_does_not_compare_them(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    routes = Routes(respx_mock)

    webhooks.ensure(HOOK_URL, LIVE_EVENTS)

    assert routes.writes == (0, 0, 0)


def test_ensure_creates_when_the_url_is_unknown(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    routes = Routes(respx_mock)

    hook = webhooks.ensure(
        "https://hooks.example.com/new", ["gate_changed"], filters={"flight_number": "LH400"}
    )

    assert routes.writes == (1, 0, 0)
    assert json.loads(routes.create.calls.last.request.content) == {
        "url": "https://hooks.example.com/new",
        "event_types": ["gate_changed"],
        "filters": {"flight_number": "LH400"},
    }
    assert isinstance(hook, Webhook)


def test_ensure_patches_when_only_active_differs(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    """The one field ``PATCH /webhooks/{id}`` can change."""

    routes = Routes(respx_mock)

    hook = webhooks.ensure(HOOK_URL, LIVE_EVENTS, active=False, filters=LIVE_FILTERS)

    assert routes.writes == (0, 1, 0)
    assert routes.update.calls.last.request.url.path == f"/v3.1/webhooks/{HOOK_ID}"
    assert json.loads(routes.update.calls.last.request.content) == {"active": False}
    assert hook.active is False
    assert hook.id == HOOK_ID


def test_ensure_recreates_when_the_events_differ(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    """Events are immutable server-side — PATCH only takes ``active``."""

    routes = Routes(respx_mock)

    webhooks.ensure(HOOK_URL, ["gate_changed"], filters=LIVE_FILTERS)

    assert routes.writes == (1, 0, 1)
    # Deleted first, so the plan's active-subscription cap is never exceeded.
    assert routes.delete.calls.last.request.url.path == f"/v3.1/webhooks/{HOOK_ID}"
    assert json.loads(routes.create.calls.last.request.content)["event_types"] == ["gate_changed"]


def test_ensure_recreates_when_the_filters_differ(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    routes = Routes(respx_mock)

    webhooks.ensure(HOOK_URL, LIVE_EVENTS, filters={"flight_number": "LH400"})

    assert routes.writes == (1, 0, 1)


def test_ensure_creates_then_disables_when_active_is_false(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    """The API always creates enabled, so a paused subscription needs two calls."""

    routes = Routes(respx_mock, listed={"count": 0, "webhooks": []})

    hook = webhooks.ensure(HOOK_URL, LIVE_EVENTS, active=False, filters=LIVE_FILTERS)

    assert routes.writes == (1, 1, 0)
    assert hook.active is False


def test_ensure_propagates_the_plan_cap_error(
    webhooks: Webhooks, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(200, json={"count": 0, "webhooks": []})
    )
    respx_mock.post(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(422, json={"detail": "Webhook limit reached (1)."})
    )

    with pytest.raises(UnprocessableEntityError):
        webhooks.ensure(HOOK_URL, LIVE_EVENTS, filters=LIVE_FILTERS)


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_create_and_list(
    async_webhooks: AsyncWebhooks, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(201, json=load_fixture("webhook_created"))
    )
    respx_mock.get(f"{TEST_BASE_URL}/webhooks").mock(
        return_value=httpx.Response(200, json=load_fixture("webhooks_list"))
    )

    hook = await async_webhooks.create(
        url=HOOK_URL, event_types=["status_changed"], filters={"flight_number": "BA117"}
    )
    rows = await async_webhooks.list()

    assert hook.id == HOOK_ID
    assert len(rows) == 2
    assert rows[0].created_at is not None
    assert rows[0].last_triggered_at is not None
    assert rows[0].last_triggered_at - rows[0].created_at == timedelta(minutes=65, seconds=22)


async def test_async_ensure_matches_the_sync_behaviour(
    async_webhooks: AsyncWebhooks, respx_mock: respx.MockRouter
) -> None:
    routes = Routes(respx_mock)

    unchanged = await async_webhooks.ensure(HOOK_URL, LIVE_EVENTS, filters=LIVE_FILTERS)
    assert routes.writes == (0, 0, 0)
    assert unchanged.id == HOOK_ID

    paused = await async_webhooks.ensure(HOOK_URL, LIVE_EVENTS, active=False, filters=LIVE_FILTERS)
    assert routes.writes == (0, 1, 0)
    assert paused.active is False

    await async_webhooks.ensure(HOOK_URL, ["gate_changed"], filters=LIVE_FILTERS)
    assert routes.writes == (1, 1, 1)


async def test_async_ensure_creates_an_unknown_url(
    async_webhooks: AsyncWebhooks, respx_mock: respx.MockRouter
) -> None:
    routes = Routes(respx_mock, listed={"count": 0, "webhooks": []})

    hook = await async_webhooks.ensure(HOOK_URL, LIVE_EVENTS, filters=LIVE_FILTERS)

    assert routes.writes == (1, 0, 0)
    assert hook.id == HOOK_ID


async def test_async_delete_and_event_types(
    async_webhooks: AsyncWebhooks, respx_mock: respx.MockRouter
) -> None:
    respx_mock.delete(f"{TEST_BASE_URL}/webhooks/{HOOK_ID}").mock(return_value=httpx.Response(204))
    respx_mock.get(f"{TEST_BASE_URL}/webhooks/events").mock(
        return_value=httpx.Response(200, json={"event_types": ALL_EVENT_TYPES})
    )

    assert await async_webhooks.delete(HOOK_ID) is None
    assert await async_webhooks.event_types() == ALL_EVENT_TYPES
