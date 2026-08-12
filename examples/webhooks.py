"""Webhook subscriptions: the full create → list → update → delete cycle.

These are the only non-GET endpoints in the API. ``create`` answers ``201``,
``delete`` answers ``204`` with no body (so the method returns ``None``), and the
retry policy never replays a ``POST`` after a 5xx — only after a ``429``, which
provably never reached the handler.

Run it with a key in the environment::

    export SKYLINK_API_KEY=sk_live_...
    python examples/webhooks.py
"""

from __future__ import annotations

from skylink_api import (
    APIStatusError,
    AuthenticationError,
    RateLimitError,
    SkyLink,
)

CALLBACK_URL = "https://hooks.example.com/skylink"


def show_event_types(sky: SkyLink) -> list[str]:
    """The event names a subscription may listen for."""

    events = sky.webhooks.event_types()
    print("Supported events:")
    for name in events:
        print(f"  {name}")
    return events


def create(sky: SkyLink) -> str:
    """Subscribe, and return the new subscription id."""

    hook = sky.webhooks.create(
        url=CALLBACK_URL,
        event_types=["status_changed", "flight_delayed", "gate_changed"],
        # Optional server-side filter; omit it to receive every matching event.
        filters={"flight_number": "BA117"},
    )
    print(f"\nCreated {hook.id}")
    print(f"  url     {hook.url}")
    print(f"  events  {', '.join(hook.event_types)}")
    print(f"  filters {hook.filters}")
    print(f"  active  {hook.active} (created {hook.created_at})")

    if hook.id is None:  # defensive: every field on a scraped payload is optional
        raise SystemExit("The API returned a subscription without an id.")
    return hook.id


def show_all(sky: SkyLink) -> None:
    """List rows carry delivery bookkeeping that the create response does not."""

    rows = sky.webhooks.list()
    print(f"\n{len(rows)} subscriptions:")
    for row in rows:
        state = "active" if row.active else "paused"
        print(
            f"  {row.id}  {state:6} failures={row.failure_count}  "
            f"last triggered {row.last_triggered_at}"
        )


def pause(sky: SkyLink, webhook_id: str) -> None:
    """PATCH toggles delivery without losing the subscription."""

    toggled = sky.webhooks.update(webhook_id, active=False)
    print(f"\nPaused {toggled.id}: active={toggled.active}")


def delete(sky: SkyLink, webhook_id: str) -> None:
    """DELETE answers 204 — there is nothing to unpack."""

    sky.webhooks.delete(webhook_id)
    print(f"Deleted {webhook_id}")


def main() -> None:
    try:
        with SkyLink() as sky:  # api_key falls back to $SKYLINK_API_KEY
            show_event_types(sky)
            webhook_id = create(sky)
            show_all(sky)
            pause(sky, webhook_id)
            delete(sky, webhook_id)
    except AuthenticationError as err:
        raise SystemExit(f"Auth failed: {err}") from err
    except RateLimitError as err:
        raise SystemExit(f"Quota exhausted, retry in {err.retry_after}s") from err
    except APIStatusError as err:
        # 422 when the callback URL or an event name is rejected — the parsed
        # per-field details are on .errors.
        for item in err.errors:
            print(f"  invalid: {item}")
        raise SystemExit(f"API error {err.status_code}: {err.message}") from err


if __name__ == "__main__":
    main()
