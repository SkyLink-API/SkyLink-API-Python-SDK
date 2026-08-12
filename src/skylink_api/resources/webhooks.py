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
* :meth:`Webhooks.ensure` is the SDK's own composite: list, compare, then create,
  patch or do nothing. It exists because the plan caps are brutal (PRO allows
  **one** active subscription) — a second ``create`` for a URL you already
  registered is a 422, not an update.

``list`` is also a method name here, which shadows the builtin inside the class
body — hence the ``builtins.list[...]`` return annotations.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, cast

from .._types import RequestOptions, RequestSpec
from ..models.webhooks import (
    Webhook,
    WebhookEvent,
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

#: What the request side accepts for an event: the literal string or the
#: equivalent :class:`~skylink_api.models.webhooks.WebhookEvent` member. Both go
#: on the wire as the same string.
WebhookEventLike = WebhookEventType | WebhookEvent


# ── builders ─────────────────────────────────────────────────────────────────


def _event_values(event_types: Iterable[WebhookEventLike]) -> builtins.list[str]:
    """Normalise events to their wire strings.

    ``WebhookEvent`` is a ``str`` enum, so this is belt and braces — but it keeps
    the JSON body free of enum members no matter which serialiser sees it.
    """

    return [event.value if isinstance(event, WebhookEvent) else str(event) for event in event_types]


def _create_spec(
    *,
    url: str,
    event_types: Sequence[WebhookEventLike],
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
            "event_types": _event_values(event_types),
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


# ── ensure() support ─────────────────────────────────────────────────────────


def _normalized_url(url: str | None) -> str:
    """Compare URLs the way the backend stores them: verbatim, minus whitespace."""

    return url.strip() if isinstance(url, str) else ""


def _normalized_filters(filters: Mapping[str, Any] | None) -> dict[str, Any]:
    """Apply the one normalisation the backend performs, so comparisons converge.

    ``create_subscription`` stores ``filters["flight_number"]`` stripped and
    upper-cased. Comparing a caller's ``"ba117"`` against the stored ``"BA117"``
    without this would report a difference on every call and re-create the
    subscription forever.
    """

    if not filters:
        return {}
    normalized = dict(filters)
    flight_number = normalized.get("flight_number")
    if isinstance(flight_number, str):
        normalized["flight_number"] = flight_number.strip().upper()
    return normalized


def _find_by_url(
    subscriptions: Iterable[WebhookSubscription], url: str
) -> WebhookSubscription | None:
    """First subscription registered for ``url`` — the identity ``ensure`` keys on.

    The API allows several subscriptions on one URL (they would all fire), but
    ``ensure`` treats the URL as the key, so the first match wins.
    """

    target = _normalized_url(url)
    for subscription in subscriptions:
        if _normalized_url(subscription.url) == target:
            return subscription
    return None


def _definition_matches(
    existing: Webhook,
    *,
    events: Sequence[str],
    filters: Mapping[str, Any] | None,
) -> bool:
    """Whether the immutable part of a subscription already says what we want.

    Events are compared as **sets** — a subscription listens to a set of events,
    and the wire order carries no meaning. ``filters=None`` means "do not care",
    so it never triggers a difference.
    """

    if set(existing.event_types) != set(events):
        return False
    if filters is None:
        return True
    return _normalized_filters(existing.filters) == _normalized_filters(filters)


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
        event_types: Sequence[WebhookEventLike],
        filters: Mapping[str, Any] | None = None,
        request_options: RequestOptions | None = None,
    ) -> Webhook:
        """Subscribe to flight status events — ``POST /webhooks`` (``201``).

        Args:
            url: Delivery endpoint. **HTTPS only**, and it must resolve to a
                public address — private and loopback hosts are rejected
                (SSRF protection), which makes local testing impossible.
            event_types: One or more of :data:`WebhookEventType` (or the matching
                :class:`~skylink_api.models.webhooks.WebhookEvent` members); an
                empty sequence is rejected.
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

    def ensure(
        self,
        url: str,
        event_types: Sequence[WebhookEventLike],
        *,
        active: bool = True,
        filters: Mapping[str, Any] | None = None,
        request_options: RequestOptions | None = None,
    ) -> Webhook:
        """Idempotent create-or-update, keyed on ``url``. Safe to call on every boot.

        The plan caps make a plain :meth:`create` unusable in a deploy script:
        PRO allows exactly **one** active subscription, so the second start of
        your service is a 422 ("Webhook limit reached"). This declares the
        subscription you want and reconciles it::

            hook = sky.webhooks.ensure(
                "https://hooks.example.com/skylink",
                [WebhookEvent.FLIGHT_DELAYED, WebhookEvent.GATE_CHANGED],
                filters={"flight_number": "BA117"},
            )

        What it does, in order:

        1. :meth:`list` and look for a subscription on the same ``url``.
        2. **No match** → :meth:`create` it (and :meth:`update` it to inactive if
           ``active=False`` — the API always creates enabled).
        3. **Match, everything equal** → return it, *no write at all*.
        4. **Match, only ``active`` differs** → :meth:`update` (``PATCH``).
        5. **Match, events or filters differ** → :meth:`delete` and re-create.
           ``PATCH /webhooks/{id}`` accepts **only** ``active``; events, filters
           and URL are immutable server-side, so replacing is the only way to
           change them. Deleting first also keeps the plan cap satisfied. The new
           subscription has a new ``id`` and loses ``failure_count`` /
           ``last_triggered_at``.

        Args:
            url: Delivery endpoint; the identity ``ensure`` matches on. The first
                subscription registered for it wins if several exist.
            event_types: The events the subscription should listen to. Compared
                as a **set** — order is not a difference.
            active: Desired delivery state. ``False`` leaves the subscription in
                place but paused.
            filters: Desired filters, normally ``{"flight_number": "BA117"}``.
                ``None`` means "do not compare filters" and, when creating,
                sends ``{}`` — which the backend rejects, so pass it for a new
                subscription. ``flight_number`` is compared case-insensitively
                because the backend upper-cases it.

        Returns:
            The subscription as it now stands. It is the existing row when
            nothing had to change (a
            :class:`~skylink_api.models.webhooks.WebhookSubscription`, with
            delivery health), or the newly created
            :class:`~skylink_api.models.webhooks.Webhook`.

        Raises:
            PermissionDeniedError: the plan does not allow webhooks (403).
            UnprocessableEntityError: bad URL, unknown event, missing
                ``filters.flight_number`` or the cap reached (422).

        Note:
            Not atomic: between the ``GET`` and the write another process could
            change the same subscription. That is fine for the deploy-time use
            this exists for, and there is no server-side upsert to do better.
        """

        events = _event_values(event_types)
        existing = _find_by_url(self.list(request_options=request_options), url)

        if existing is not None and _definition_matches(existing, events=events, filters=filters):
            if existing.active == active:
                return existing  # Already exactly what was asked for — no write.
            if existing.id is not None:
                self.update(existing.id, active=active, request_options=request_options)
            return existing.model_copy(update={"active": active})

        if existing is not None and existing.id is not None:
            # Events/filters are immutable server-side: replace, do not patch.
            self.delete(existing.id, request_options=request_options)

        created = self.create(
            url=url,
            event_types=event_types,
            filters=filters,
            request_options=request_options,
        )
        if not active and created.id is not None:
            self.update(created.id, active=False, request_options=request_options)
            return created.model_copy(update={"active": False})
        return created

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
        event_types: Sequence[WebhookEventLike],
        filters: Mapping[str, Any] | None = None,
        request_options: RequestOptions | None = None,
    ) -> Webhook:
        """Subscribe to flight status events — ``POST /webhooks`` (``201``).

        Args:
            url: Delivery endpoint. **HTTPS only**, and it must resolve to a
                public address — private and loopback hosts are rejected
                (SSRF protection), which makes local testing impossible.
            event_types: One or more of :data:`WebhookEventType` (or the matching
                :class:`~skylink_api.models.webhooks.WebhookEvent` members); an
                empty sequence is rejected.
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

    async def ensure(
        self,
        url: str,
        event_types: Sequence[WebhookEventLike],
        *,
        active: bool = True,
        filters: Mapping[str, Any] | None = None,
        request_options: RequestOptions | None = None,
    ) -> Webhook:
        """Idempotent create-or-update, keyed on ``url``. Safe to call on every boot.

        Mirror of :meth:`Webhooks.ensure`; see it for the full reconciliation
        rules. In short: list → match on ``url`` → return unchanged, ``PATCH``
        ``active``, or delete-and-recreate when the events or filters differ
        (they are immutable server-side)::

            hook = await sky.webhooks.ensure(
                "https://hooks.example.com/skylink",
                [WebhookEvent.FLIGHT_DELAYED],
                filters={"flight_number": "BA117"},
            )

        Args:
            url: Delivery endpoint; the identity to match on.
            event_types: Desired events, compared as a set.
            active: Desired delivery state.
            filters: Desired filters; ``None`` skips the comparison.

        Returns:
            The subscription as it now stands.

        Raises:
            PermissionDeniedError: the plan does not allow webhooks (403).
            UnprocessableEntityError: bad URL, unknown event, missing
                ``filters.flight_number`` or the cap reached (422).
        """

        events = _event_values(event_types)
        existing = _find_by_url(await self.list(request_options=request_options), url)

        if existing is not None and _definition_matches(existing, events=events, filters=filters):
            if existing.active == active:
                return existing  # Already exactly what was asked for — no write.
            if existing.id is not None:
                await self.update(existing.id, active=active, request_options=request_options)
            return existing.model_copy(update={"active": active})

        if existing is not None and existing.id is not None:
            # Events/filters are immutable server-side: replace, do not patch.
            await self.delete(existing.id, request_options=request_options)

        created = await self.create(
            url=url,
            event_types=event_types,
            filters=filters,
            request_options=request_options,
        )
        if not active and created.id is not None:
            await self.update(created.id, active=False, request_options=request_options)
            return created.model_copy(update={"active": False})
        return created

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
