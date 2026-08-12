"""Live webhook CRUD, ``ensure()`` reconciliation and the plan's subscription cap.

Webhooks are the only writing part of the API, which makes them the only part a
mock cannot honestly stand in for: the immutability of ``event_types``, the cap
that counts *active* rows, the ``204`` with no body, the ``422`` that means "you
already have as many as your plan allows" — none of that is visible offline.
:mod:`tests.integration.test_live` walks the plain create/list/update/delete
cycle; this file covers what it cannot, and pays for it with real writes:

* :func:`test_ensure_creates_reuses_patches_and_replaces` — every branch of
  :meth:`~skylink_api.resources.webhooks.Webhooks.ensure`, each one pinned by the
  **number of requests it makes**, so "no-op" means no write actually happened
  rather than "the return value looked unchanged";
* :func:`test_plan_limit_is_reported_as_unprocessable_entity` — fills the plan's
  cap and records where it stops (ULTRA: 3);
* :func:`test_unknown_and_malformed_ids` — the two different rejections of a bad
  id, which callers have to tell apart.

**Everything created here is deleted again**, by a fixture finalizer that runs
whether the test passed, failed or exploded, and that additionally sweeps any
subscription whose URL carries :data:`MARKER` (a leak from an earlier crashed
run). Delivery targets are ``https://example.com/skylink-sdk-test-<uuid>``: a
public host — the backend's SSRF validator rejects private and loopback
addresses, and resolves the name — that has nothing listening for us, so nobody
is ever actually called.

Gating: writes need a plan that allows webhooks, so the file is armed by a
**direct** key (Polar licence key, PRO/ULTRA/MEGA) or by an explicit staging base
URL. On RapidAPI the cap comes from the ``X-RapidAPI-Subscription`` header the
proxy injects, and ``resolve_webhook_limit`` maps anything it does not recognise
to zero — a BASIC plan, but also a bespoke name such as ``CUSTOM-Alexandr``
(``CUSTOM`` on its own is the only wildcard). Either way ``POST /webhooks``
answers ``403`` and the file would only ever skip::

    $env:SKYLINK_TEST_PROVIDER="direct"
    $env:SKYLINK_TEST_API_KEY="<direct key>"
    ./.venv/Scripts/python.exe -m pytest tests/integration/test_live_webhooks.py -rs -v

Note:
    There is no ``GET /webhooks/{id}`` on the backend (``routers/v31/webhooks.py``
    declares ``POST ""``, ``GET ""``, ``PATCH /{id}``, ``DELETE /{id}`` and
    ``GET /events`` — that is all), so "read one subscription back" is a
    :meth:`~skylink_api.resources.webhooks.Webhooks.list` plus a lookup by id,
    which is exactly what the SDK offers and what these tests do.
"""

from __future__ import annotations

import uuid
import warnings
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from typing import Any

import pytest

from skylink_api import (
    WEBHOOK_EVENTS,
    APIStatusError,
    NotFoundError,
    RateLimitInfo,
    SkyLink,
    UnprocessableEntityError,
    Webhook,
    WebhookEvent,
    WebhookSubscription,
    WebhookToggleResponse,
)

from .conftest import API_KEY, BASE_URL, PLAN_ERRORS, PROVIDER, describe, tolerating

#: Writes need the webhook entitlement: a direct key, or a staging instance.
WRITES_ARMED = bool(BASE_URL) or (PROVIDER == "direct" and bool(API_KEY))

SKIP_REASON = (
    "live webhook CRUD needs SKYLINK_TEST_PROVIDER=direct with a direct key on a "
    "plan that allows webhooks (PRO/ULTRA/MEGA), or SKYLINK_TEST_BASE_URL for a "
    "staging instance — on RapidAPI, POST /webhooks is refused with 403 for BASIC "
    "and for any subscription name resolve_webhook_limit() does not recognise"
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not WRITES_ARMED, reason=SKIP_REASON),
]

#: Every URL this file registers carries it, so the sweep can recognise its own
#: litter without ever touching a subscription somebody else created.
MARKER = "skylink-sdk-test-"

#: The flight the test subscriptions watch. Lower case on purpose: the backend
#: upper-cases ``filters.flight_number``, and ``ensure`` has to compare across
#: that normalisation instead of re-creating the subscription forever.
FLIGHT_NUMBER = "ba117"

#: Highest number of subscriptions the cap test will create before giving up.
#: Above every documented plan cap (MEGA: 10) so an unexpectedly generous plan is
#: reported rather than silently truncated.
MAX_CAP_PROBE = 12


def _report(title: str, *lines: object) -> None:
    """Print a labelled block of live findings (visible under ``pytest -s``)."""

    print(f"\n[live] {title}")
    for line in lines:
        print(f"       {line}")


# ── sandbox ──────────────────────────────────────────────────────────────────


class WebhookSandbox:
    """Creates subscriptions, remembers them, and guarantees they are removed."""

    def __init__(self, sky: SkyLink) -> None:
        self.sky = sky
        self.ids: list[str] = []

    def url(self) -> str:
        """A fresh, unique, public-but-dead delivery target."""

        return f"https://example.com/{MARKER}{uuid.uuid4()}"

    def track(self, hook: Webhook) -> Webhook:
        """Register a subscription for deletion at teardown."""

        if hook.id is not None and hook.id not in self.ids:
            self.ids.append(hook.id)
        return hook

    def create(
        self,
        *,
        url: str | None = None,
        event_types: Sequence[Any] = (WebhookEvent.FLIGHT_DELAYED,),
        filters: Mapping[str, Any] | None = None,
    ) -> Webhook:
        """``create()`` that cannot leak — the id is tracked before it is returned."""

        return self.track(
            self.sky.webhooks.create(
                url=url if url is not None else self.url(),
                event_types=list(event_types),
                filters=dict(filters) if filters is not None else {"flight_number": FLIGHT_NUMBER},
            )
        )

    def ensure(self, url: str, event_types: Sequence[Any], **kwargs: Any) -> Webhook:
        """``ensure()`` that cannot leak — including the row it re-created."""

        kwargs.setdefault("filters", {"flight_number": FLIGHT_NUMBER})
        return self.track(self.sky.webhooks.ensure(url, list(event_types), **kwargs))

    def mine(self) -> list[WebhookSubscription]:
        """Live subscriptions registered by this suite (by :data:`MARKER`)."""

        return [hook for hook in self.sky.webhooks.list() if MARKER in (hook.url or "")]

    def cleanup(self) -> None:
        """Delete everything this suite created; then prove nothing is left.

        Runs on the way out of every test, passed or failed. Tracked ids go
        first — they are the only ones a plan-gated ``403`` could have kept us
        from listing — and then a sweep by :data:`MARKER` picks up anything an
        earlier crashed run left behind.
        """

        for webhook_id in reversed(self.ids):
            try:
                self.sky.webhooks.delete(webhook_id)
            except NotFoundError:
                pass  # already gone: deleted by the test, or replaced by ensure()
            except APIStatusError as exc:
                warnings.warn(
                    f"webhook cleanup: DELETE {webhook_id} failed with {describe(exc)}",
                    RuntimeWarning,
                    stacklevel=1,
                )
        self.ids.clear()

        try:
            leftovers = self.mine()
        except APIStatusError as exc:
            # No entitlement to list (403) means there was nothing to create either.
            warnings.warn(
                f"webhook cleanup: could not verify, GET /webhooks failed with {describe(exc)}",
                RuntimeWarning,
                stacklevel=1,
            )
            return

        for hook in leftovers:
            if hook.id is not None:
                with suppress(NotFoundError):
                    self.sky.webhooks.delete(hook.id)

        remaining = [hook.url for hook in self.mine()]
        assert not remaining, f"webhook cleanup left subscriptions behind: {remaining}"


@pytest.fixture
def sandbox(sky: SkyLink) -> Iterator[WebhookSandbox]:
    """A :class:`WebhookSandbox` whose subscriptions are always cleaned up."""

    box = WebhookSandbox(sky)
    try:
        yield box
    finally:
        box.cleanup()


class RequestCounter:
    """Counts live responses, to tell "no write" from "looked unchanged".

    Every production response carries quota headers on both channels
    (``X-RateLimit-Requests-*`` on RapidAPI, ``X-RateLimit-*`` on direct), so
    ``on_rate_limit`` fires exactly once per response and doubles as a request
    counter that needs no transport surgery. Only a custom ``base_url`` — staging
    behind neither gateway — may leave the count at ``0``; on a production
    channel an empty counter is itself a failure, see :meth:`assert_requests`.
    """

    def __init__(self, sky: SkyLink) -> None:
        self.sky = sky
        self.seen: list[RateLimitInfo] = []
        self._stop = sky.on_rate_limit(self.seen.append)

    def reset(self) -> None:
        self.seen.clear()

    @property
    def count(self) -> int:
        return len(self.seen)

    def assert_requests(self, expected: int, what: str) -> None:
        if not self.seen:
            # A production gateway always sends quota headers, so an empty counter
            # there is the SDK failing to recognise them — not a licence to skip
            # the assertion, which would quietly hollow out this whole test.
            assert BASE_URL, (
                f"{what}: no quota headers seen on the {PROVIDER} channel, so the "
                "request count cannot be trusted (does the SDK parse this "
                "gateway's X-RateLimit-* spelling?)"
            )
            return  # staging behind neither gateway: nothing to conclude
        assert self.count == expected, (
            f"{what}: expected {expected} request(s), the client saw {self.count}"
        )

    def close(self) -> None:
        self._stop()


@pytest.fixture
def requests_made(sky: SkyLink) -> Iterator[RequestCounter]:
    counter = RequestCounter(sky)
    try:
        yield counter
    finally:
        counter.close()


# ── 1. read-only ─────────────────────────────────────────────────────────────


def test_event_types_match_the_sdk_constant(sky: SkyLink) -> None:
    """``GET /webhooks/events`` still lists exactly the six events the SDK types.

    The one endpoint here that costs nothing and needs no entitlement — and the
    canary for :data:`skylink_api.WEBHOOK_EVENTS` and the ``WebhookEvent`` enum
    drifting away from the deployment.
    """

    with tolerating("webhooks.event_types", errors=PLAN_ERRORS):
        events = sky.webhooks.event_types()

        assert sorted(events) == sorted(WEBHOOK_EVENTS), (
            f"the deployment's event types {sorted(events)} no longer match the "
            f"SDK's {sorted(WEBHOOK_EVENTS)}"
        )
        assert sorted(events) == sorted(member.value for member in WebhookEvent)
        _report("webhooks.event_types()", ", ".join(sorted(events)))


def test_list_returns_typed_subscriptions(sky: SkyLink) -> None:
    """``GET /webhooks`` unwraps its envelope into the delivery-health model."""

    with tolerating("webhooks.list", errors=PLAN_ERRORS):
        hooks = sky.webhooks.list()

        assert isinstance(hooks, list)
        assert all(isinstance(hook, WebhookSubscription) for hook in hooks)
        _report("webhooks.list()", f"{len(hooks)} subscription(s) on this key")


# ── 2. the write cycle ───────────────────────────────────────────────────────


def test_create_list_update_delete(sandbox: WebhookSandbox, sky: SkyLink) -> None:
    """create (201) → list → update (PATCH) → delete (204) → gone.

    The read-back is a ``list()`` and a lookup by id: the backend has no
    ``GET /webhooks/{id}`` route at all.
    """

    with tolerating("webhooks CRUD", errors=PLAN_ERRORS):
        url = sandbox.url()
        created = sandbox.create(url=url, event_types=[WebhookEvent.STATUS_CHANGED])

        assert isinstance(created, Webhook)
        assert created.id, "the API must return the subscription id"
        assert created.url == url
        assert created.active is True, "a new subscription is always created enabled"
        assert created.created_at is not None
        # Sent lower case, stored upper case — the normalisation ``ensure`` has to
        # cope with, pinned here so a change on the backend is caught once.
        assert created.filters == {"flight_number": FLIGHT_NUMBER.upper()}

        webhook_id = created.id

        # Read back: the list row is a strict superset of the create body.
        row = _find(sky, webhook_id)
        assert row is not None, "the created subscription is missing from list()"
        assert isinstance(row, WebhookSubscription)
        assert row.url == url
        assert row.event_types == ["status_changed"]
        assert row.active is True
        assert row.failure_count == 0
        assert row.last_triggered_at is None, "a fresh subscription has never fired"

        # PATCH: the acknowledgement is two fields, not the subscription.
        toggled = sky.webhooks.update(webhook_id, active=False)
        assert isinstance(toggled, WebhookToggleResponse)
        assert toggled.id == webhook_id
        assert toggled.active is False

        paused = _find(sky, webhook_id)
        assert paused is not None and paused.active is False
        assert paused.event_types == ["status_changed"], "PATCH must not touch the events"

        # DELETE answers 204 with an empty body: reaching the next line at all is
        # the proof that nothing tried to decode it.
        sky.webhooks.delete(webhook_id)
        assert _find(sky, webhook_id) is None


def test_unknown_and_malformed_ids(sky: SkyLink) -> None:
    """Two different rejections a caller has to tell apart.

    An id that is not a UUID never reaches the store (``422`` from
    ``validate_uuid``); a well-formed id that is unknown *or owned by another
    key* is a ``404`` — the API deliberately does not distinguish those two.
    """

    unknown = str(uuid.uuid4())

    with tolerating("webhooks bad ids", errors=PLAN_ERRORS):
        with pytest.raises(NotFoundError):
            sky.webhooks.delete(unknown)
        with pytest.raises(NotFoundError):
            sky.webhooks.update(unknown, active=True)

        with pytest.raises(UnprocessableEntityError) as excinfo:
            sky.webhooks.update("not-a-uuid", active=True)
        assert "uuid" in str(excinfo.value).lower()


def test_create_rejects_what_the_backend_validates(sandbox: WebhookSandbox) -> None:
    """The three ``422`` s of a bad create, each with a usable message."""

    with tolerating("webhooks create validation", errors=PLAN_ERRORS):
        with pytest.raises(UnprocessableEntityError) as missing_filter:
            sandbox.create(filters={})
        assert "flight_number" in str(missing_filter.value)

        with pytest.raises(UnprocessableEntityError) as bad_event:
            sandbox.create(event_types=["not_an_event"])
        assert "event" in str(bad_event.value).lower()

        with pytest.raises(UnprocessableEntityError) as plain_http:
            sandbox.create(url=f"http://example.com/{MARKER}{uuid.uuid4()}")
        assert "https" in str(plain_http.value).lower()


# ── 3. ensure() ──────────────────────────────────────────────────────────────


def test_ensure_creates_reuses_patches_and_replaces(
    sandbox: WebhookSandbox, sky: SkyLink, requests_made: RequestCounter
) -> None:
    """All four branches of ``ensure``, each pinned by its request count.

    The request count is what makes the second branch meaningful: a no-op that
    silently deleted and re-created the subscription would return an equal-looking
    object. Counting responses shows the single ``GET`` and nothing else.

    Branch 3 is the interesting one. ``PATCH /webhooks/{id}`` takes **only**
    ``active`` — ``WebhookPatch`` on the backend has exactly that one field — so
    changing the events cannot be a patch; ``ensure`` deletes and re-creates,
    which also keeps the plan cap satisfied while it does so. The new ``id`` is
    the evidence.
    """

    with tolerating("webhooks.ensure", errors=PLAN_ERRORS):
        url = sandbox.url()

        # 1. Nothing registered for this URL yet → GET + POST.
        requests_made.reset()
        created = sandbox.ensure(url, [WebhookEvent.FLIGHT_DELAYED])
        requests_made.assert_requests(2, "ensure() creating")
        assert created.id and created.url == url
        assert created.active is True
        assert set(created.event_types) == {"flight_delayed"}
        first_id = created.id

        # 2. Same declaration → no write at all, same row handed back.
        requests_made.reset()
        again = sandbox.ensure(url, [WebhookEvent.FLIGHT_DELAYED])
        requests_made.assert_requests(1, "ensure() no-op")
        assert again.id == first_id
        assert again.created_at == created.created_at
        assert isinstance(again, WebhookSubscription), (
            "an unchanged subscription comes back as the list row, with delivery health"
        )
        # Filters were compared across the backend's upper-casing, not re-created.
        assert again.filters == {"flight_number": FLIGHT_NUMBER.upper()}
        assert len(sandbox.mine()) == 1, "a no-op must not have added a second row"

        # 3. Only ``active`` differs → PATCH, id preserved.
        requests_made.reset()
        paused = sandbox.ensure(url, [WebhookEvent.FLIGHT_DELAYED], active=False)
        requests_made.assert_requests(2, "ensure() patching active")
        assert paused.id == first_id, "toggling active must not replace the subscription"
        assert paused.active is False
        stored = _find(sky, first_id)
        assert stored is not None and stored.active is False

        # Re-enable, so the replacement below starts from a normal state.
        sky.webhooks.update(first_id, active=True)

        # 4. Events differ → immutable server-side → DELETE + POST, new id.
        requests_made.reset()
        replaced = sandbox.ensure(url, [WebhookEvent.FLIGHT_DELAYED, WebhookEvent.GATE_CHANGED])
        requests_made.assert_requests(3, "ensure() replacing")
        assert replaced.id and replaced.id != first_id, (
            "changed events must produce a new subscription, not a patched one"
        )
        assert set(replaced.event_types) == {"flight_delayed", "gate_changed"}
        assert replaced.active is True

        rows = sandbox.mine()
        assert [hook.id for hook in rows] == [replaced.id], (
            "the replaced subscription must be gone, not left alongside the new one"
        )
        assert _find(sky, first_id) is None
        _report(
            "webhooks.ensure()",
            f"created {first_id}",
            f"replaced by {replaced.id} after the event set changed",
        )


def test_ensure_survives_a_changed_filter(sandbox: WebhookSandbox) -> None:
    """A different ``filters`` is a difference too — same replace path."""

    with tolerating("webhooks.ensure filters", errors=PLAN_ERRORS):
        url = sandbox.url()
        first = sandbox.ensure(
            url, [WebhookEvent.FLIGHT_LANDED], filters={"flight_number": "ba117"}
        )
        second = sandbox.ensure(
            url, [WebhookEvent.FLIGHT_LANDED], filters={"flight_number": "ba118"}
        )

        assert first.id and second.id and second.id != first.id
        assert second.filters == {"flight_number": "BA118"}
        assert len(sandbox.mine()) == 1

        # ...while the *same* filter in another case is not a difference.
        third = sandbox.ensure(
            url, [WebhookEvent.FLIGHT_LANDED], filters={"flight_number": "BA118"}
        )
        assert third.id == second.id, (
            "flight_number is upper-cased server-side; ensure must compare across that"
        )


# ── 4. the plan cap ──────────────────────────────────────────────────────────


def test_plan_limit_is_reported_as_unprocessable_entity(sandbox: WebhookSandbox) -> None:
    """Creating past the plan's cap is a ``422``, and only *active* rows count.

    The cap is per plan (``_MAX_BY_PLAN`` in ``services/v31/webhook_service.py``:
    BASIC 0, PRO 1, ULTRA 3, MEGA 10) and it is enforced against
    ``COUNT(*) WHERE active = 1`` — so a paused subscription frees a slot without
    being deleted, which is asserted here because it is the documented way out of
    a full account.
    """

    with tolerating("webhooks plan cap", errors=PLAN_ERRORS):
        created: list[Webhook] = []
        refusal: UnprocessableEntityError | None = None

        for _ in range(MAX_CAP_PROBE):
            try:
                created.append(sandbox.create())
            except UnprocessableEntityError as exc:
                refusal = exc
                break

        if not created:
            pytest.skip(
                "this key is already at its webhook cap before the test created "
                f"anything ({describe(refusal) if refusal else 'unknown'})"
            )

        assert refusal is not None, (
            f"created {len(created)} subscriptions without hitting a cap; either the "
            f"plan allows more than {MAX_CAP_PROBE} or the limit is not enforced"
        )
        assert "limit" in str(refusal).lower()
        assert str(len(created)) in str(refusal), (
            f"the 422 should name the cap it enforced: {refusal}"
        )
        assert len(sandbox.mine()) == len(created)
        _report(
            "webhook plan cap",
            f"cap = {len(created)} active subscription(s)",
            f"refusal = {describe(refusal)}",
        )

        # A paused subscription does not count towards the cap: pausing one must
        # make room for exactly one more.
        first_id = created[0].id
        assert first_id is not None
        sandbox.sky.webhooks.update(first_id, active=False)
        extra = sandbox.create()
        assert extra.id is not None
        assert len(sandbox.mine()) == len(created) + 1, (
            "the paused subscription should still exist alongside the new one"
        )


# ── helpers ──────────────────────────────────────────────────────────────────


def _find(sky: SkyLink, webhook_id: str) -> WebhookSubscription | None:
    """The subscription with ``webhook_id``, or ``None`` — there is no GET by id."""

    for hook in sky.webhooks.list():
        if hook.id == webhook_id:
            return hook
    return None
