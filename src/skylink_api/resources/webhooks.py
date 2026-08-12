"""Webhook subscriptions — the only non-GET endpoints in the API.

Mechanics specific to this namespace:

* :meth:`Webhooks.create` is a ``POST`` that answers ``201``. The SDK's retry
  policy replays unsafe methods on ``429`` only, so a create is never duplicated
  by a retry.
* :meth:`Webhooks.delete` is a ``DELETE`` answering ``204`` with an empty body,
  which the spec declares as ``response_kind="none"`` and the method returns as
  ``None`` — nothing is parsed.
* :meth:`Webhooks.list` and :meth:`Webhooks.event_types` unwrap their envelopes
  and hand back plain lists.

``list`` is also a method name here, which shadows the builtin inside the class
body — hence the ``builtins.list[...]`` return annotations.
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, cast

from .._types import RequestOptions, RequestSpec
from ..models.webhooks import (
    Webhook,
    WebhookEventTypesResponse,
    WebhookListResponse,
    WebhookSubscription,
    WebhookToggleResponse,
)

if TYPE_CHECKING:
    from .._client import AsyncSkyLink, SkyLink

__all__ = ["AsyncWebhooks", "Webhooks"]

#: The six events a subscription can listen for.
#:
#: * ``status_changed`` — catch-all: any tracked field changed.
#: * ``flight_delayed`` — status indicates a delay, or the actual time moved.
#: * ``flight_cancelled`` — the flight was cancelled.
#: * ``flight_boarding`` — boarding is in progress.
#: * ``flight_landed`` — the flight has landed.
#: * ``gate_changed`` — a departure or arrival gate changed.
WebhookEventType = Literal[
    "status_changed",
    "flight_delayed",
    "flight_cancelled",
    "flight_boarding",
    "flight_landed",
    "gate_changed",
]


# ── builders ─────────────────────────────────────────────────────────────────


def _create_spec(
    *,
    url: str,
    event_types: Sequence[WebhookEventType],
    filters: Mapping[str, Any] | None = None,
) -> RequestSpec:
    """``POST /webhooks`` → ``201``.

    ``filters`` is always sent, as ``{}`` when omitted: the backend field is a
    ``dict`` with a default, and ``null`` would be a validation error.
    """

    return RequestSpec(
        method="POST",
        path="/webhooks",
        json_body={
            "url": url,
            "event_types": list(event_types),
            "filters": dict(filters) if filters is not None else {},
        },
        cast_to=Webhook,
    )


def _list_spec() -> RequestSpec:
    """``GET /webhooks`` → ``{count, webhooks[]}``."""

    return RequestSpec(method="GET", path="/webhooks", cast_to=WebhookListResponse)


def _update_spec(webhook_id: str, *, active: bool) -> RequestSpec:
    """``PATCH /webhooks/{id}`` → ``{id, active}``."""

    return RequestSpec(
        method="PATCH",
        path=f"/webhooks/{webhook_id}",
        json_body={"active": active},
        cast_to=WebhookToggleResponse,
    )


def _delete_spec(webhook_id: str) -> RequestSpec:
    """``DELETE /webhooks/{id}`` → ``204`` with no body."""

    return RequestSpec(
        method="DELETE",
        path=f"/webhooks/{webhook_id}",
        response_kind="none",
    )


def _event_types_spec() -> RequestSpec:
    """``GET /webhooks/events`` → ``{event_types[]}``."""

    return RequestSpec(
        method="GET",
        path="/webhooks/events",
        cast_to=WebhookEventTypesResponse,
    )


# ── sync ─────────────────────────────────────────────────────────────────────


class Webhooks:
    """``sky.webhooks`` — push notifications for flight status changes.

    Access it through the client rather than constructing it directly::

        with SkyLink(api_key="...") as sky:
            hook = sky.webhooks.create(
                url="https://hooks.example.com/skylink",
                event_types=["flight_delayed", "gate_changed"],
                filters={"flight_number": "BA117"},
            )
            sky.webhooks.delete(hook.id)

    Subscriptions belong to the API key that created them, so a key can only see
    and modify its own. Delivery is driven by a 5-minute poll of the same source
    as ``flight_status``, and a subscription is disabled automatically after 10
    consecutive delivery failures.
    """

    def __init__(self, client: SkyLink) -> None:
        self._client = client

    def create(
        self,
        *,
        url: str,
        event_types: Sequence[WebhookEventType],
        filters: Mapping[str, Any] | None = None,
        request_options: RequestOptions | None = None,
    ) -> Webhook:
        """Subscribe to flight status events — ``POST /webhooks`` (``201``).

        Args:
            url: Delivery endpoint. **HTTPS only**, and it must resolve to a
                public address — private and loopback hosts are rejected
                (SSRF protection), which makes local testing impossible.
            event_types: One or more of :data:`WebhookEventType`; an empty
                sequence is rejected.
            filters: Which flight to watch. ``{"flight_number": "BA117"}`` is
                effectively required — the backend rejects a create without it.
                The number is upper-cased server-side, so the echoed value may
                differ from what was sent.

        Returns:
            The created :class:`~skylink_api.models.webhooks.Webhook`. It is a
            strict subset of a :meth:`list` row — ``last_triggered_at`` and
            ``failure_count`` only exist once the subscription has a history.

        Raises:
            PermissionDeniedError: the plan does not allow webhooks at all
                (RapidAPI BASIC → 403).
            UnprocessableEntityError: bad URL, unknown event type, missing
                ``filters.flight_number``, or the plan's active-subscription cap
                is already reached — 1 on PRO, 3 on ULTRA, 10 on MEGA (422).
        """

        spec = _create_spec(url=url, event_types=event_types, filters=filters)
        return cast(Webhook, self._client.execute(spec, request_options))

    def list(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> builtins.list[WebhookSubscription]:
        """Your subscriptions, newest first — ``GET /webhooks``.

        Returns:
            The subscriptions themselves. The wire format is an envelope
            ``{"count": n, "webhooks": [...]}``; the count is redundant with
            ``len()`` so the SDK hands back the list. Rows are two fields wider
            than :meth:`create`'s result (``last_triggered_at``,
            ``failure_count``), hence
            :class:`~skylink_api.models.webhooks.WebhookSubscription`.

        Note:
            Inactive subscriptions are listed too — check ``active``. A hook with
            ``failure_count`` at 10 was disabled by the dispatcher.
        """

        response = cast(WebhookListResponse, self._client.execute(_list_spec(), request_options))
        return response.webhooks

    def update(
        self,
        webhook_id: str,
        *,
        active: bool,
        request_options: RequestOptions | None = None,
    ) -> WebhookToggleResponse:
        """Enable or disable a subscription — ``PATCH /webhooks/{id}``.

        The only mutable property. To change the URL, events or filters, delete
        the subscription and create a new one.

        Args:
            webhook_id: UUID from :meth:`create` or :meth:`list`.
            active: ``False`` pauses delivery without losing the subscription.

        Returns:
            A two-field acknowledgement (``id``, ``active``) — **not** the full
            subscription. Call :meth:`list` if the rest is needed.

        Raises:
            NotFoundError: unknown ID, or it belongs to another API key — the
                API does not distinguish the two (404).
            UnprocessableEntityError: ``webhook_id`` is not a UUID (422).
        """

        spec = _update_spec(webhook_id, active=active)
        return cast(WebhookToggleResponse, self._client.execute(spec, request_options))

    def delete(
        self,
        webhook_id: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> None:
        """Delete a subscription — ``DELETE /webhooks/{id}`` (``204``).

        Args:
            webhook_id: UUID from :meth:`create` or :meth:`list`.

        Returns:
            ``None``. The API answers ``204`` with an empty body, so there is
            nothing to parse and nothing to return.

        Raises:
            NotFoundError: unknown ID, or it belongs to another API key (404).
            UnprocessableEntityError: ``webhook_id`` is not a UUID (422).
        """

        self._client.execute(_delete_spec(webhook_id), request_options)
        return None

    def event_types(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> builtins.list[str]:
        """Supported event names — ``GET /webhooks/events``.

        Returns:
            The event names, sorted. The wire format wraps them in
            ``{"event_types": [...]}``; the SDK unwraps it.

        Note:
            Handy as a runtime check that the SDK's
            :data:`WebhookEventType` literal still matches the deployment.
        """

        spec = _event_types_spec()
        response = cast(WebhookEventTypesResponse, self._client.execute(spec, request_options))
        return response.event_types


# ── async ────────────────────────────────────────────────────────────────────


class AsyncWebhooks:
    """``sky.webhooks`` on :class:`~skylink_api.AsyncSkyLink`.

    Mirror of :class:`Webhooks`; see it for the endpoint documentation::

        async with AsyncSkyLink(api_key="...") as sky:
            hooks = await sky.webhooks.list()
    """

    def __init__(self, client: AsyncSkyLink) -> None:
        self._client = client

    async def create(
        self,
        *,
        url: str,
        event_types: Sequence[WebhookEventType],
        filters: Mapping[str, Any] | None = None,
        request_options: RequestOptions | None = None,
    ) -> Webhook:
        """Subscribe to flight status events — ``POST /webhooks`` (``201``).

        Args:
            url: Delivery endpoint. **HTTPS only**, and it must resolve to a
                public address — private and loopback hosts are rejected
                (SSRF protection), which makes local testing impossible.
            event_types: One or more of :data:`WebhookEventType`; an empty
                sequence is rejected.
            filters: Which flight to watch. ``{"flight_number": "BA117"}`` is
                effectively required — the backend rejects a create without it.
                The number is upper-cased server-side, so the echoed value may
                differ from what was sent.

        Returns:
            The created :class:`~skylink_api.models.webhooks.Webhook`. It is a
            strict subset of a :meth:`list` row — ``last_triggered_at`` and
            ``failure_count`` only exist once the subscription has a history.

        Raises:
            PermissionDeniedError: the plan does not allow webhooks at all
                (RapidAPI BASIC → 403).
            UnprocessableEntityError: bad URL, unknown event type, missing
                ``filters.flight_number``, or the plan's active-subscription cap
                is already reached — 1 on PRO, 3 on ULTRA, 10 on MEGA (422).
        """

        spec = _create_spec(url=url, event_types=event_types, filters=filters)
        result: Any = await self._client.execute(spec, request_options)
        return cast(Webhook, result)

    async def list(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> builtins.list[WebhookSubscription]:
        """Your subscriptions, newest first — ``GET /webhooks``.

        Returns:
            The subscriptions themselves. The wire format is an envelope
            ``{"count": n, "webhooks": [...]}``; the count is redundant with
            ``len()`` so the SDK hands back the list. Rows are two fields wider
            than :meth:`create`'s result (``last_triggered_at``,
            ``failure_count``), hence
            :class:`~skylink_api.models.webhooks.WebhookSubscription`.

        Note:
            Inactive subscriptions are listed too — check ``active``. A hook with
            ``failure_count`` at 10 was disabled by the dispatcher.
        """

        result: Any = await self._client.execute(_list_spec(), request_options)
        return cast(WebhookListResponse, result).webhooks

    async def update(
        self,
        webhook_id: str,
        *,
        active: bool,
        request_options: RequestOptions | None = None,
    ) -> WebhookToggleResponse:
        """Enable or disable a subscription — ``PATCH /webhooks/{id}``.

        The only mutable property. To change the URL, events or filters, delete
        the subscription and create a new one.

        Args:
            webhook_id: UUID from :meth:`create` or :meth:`list`.
            active: ``False`` pauses delivery without losing the subscription.

        Returns:
            A two-field acknowledgement (``id``, ``active``) — **not** the full
            subscription. Call :meth:`list` if the rest is needed.

        Raises:
            NotFoundError: unknown ID, or it belongs to another API key — the
                API does not distinguish the two (404).
            UnprocessableEntityError: ``webhook_id`` is not a UUID (422).
        """

        spec = _update_spec(webhook_id, active=active)
        result: Any = await self._client.execute(spec, request_options)
        return cast(WebhookToggleResponse, result)

    async def delete(
        self,
        webhook_id: str,
        *,
        request_options: RequestOptions | None = None,
    ) -> None:
        """Delete a subscription — ``DELETE /webhooks/{id}`` (``204``).

        Args:
            webhook_id: UUID from :meth:`create` or :meth:`list`.

        Returns:
            ``None``. The API answers ``204`` with an empty body, so there is
            nothing to parse and nothing to return.

        Raises:
            NotFoundError: unknown ID, or it belongs to another API key (404).
            UnprocessableEntityError: ``webhook_id`` is not a UUID (422).
        """

        await self._client.execute(_delete_spec(webhook_id), request_options)
        return None

    async def event_types(
        self,
        *,
        request_options: RequestOptions | None = None,
    ) -> builtins.list[str]:
        """Supported event names — ``GET /webhooks/events``.

        Returns:
            The event names, sorted. The wire format wraps them in
            ``{"event_types": [...]}``; the SDK unwraps it.

        Note:
            Handy as a runtime check that the SDK's
            :data:`WebhookEventType` literal still matches the deployment.
        """

        result: Any = await self._client.execute(_event_types_spec(), request_options)
        return cast(WebhookEventTypesResponse, result).event_types
