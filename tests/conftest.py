"""Shared pytest fixtures.

Network is never touched: every test either drives ``respx`` or a hand rolled
``httpx.MockTransport``. Sleeping is never real either — the clients take an
injectable ``sleep``.

The shared clients pin ``provider="direct"`` on purpose. The client default is
``"rapidapi"`` (see ``tests/test_config.py``, which covers both channels), but the
per-namespace suites assert full request paths, and the direct channel is the
stricter target: its ``/v3.1`` prefix catches a lost or duplicated version segment
that a prefix-less RapidAPI URL could not. Anything genuinely channel specific —
base URLs, auth headers, env fallback — lives in ``test_config.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._types import Provider

FIXTURES_DIR = Path(__file__).parent / "fixtures"

TEST_API_KEY = "test-key"
TEST_PROVIDER: Provider = "direct"
TEST_BASE_URL = "https://data.skylinkapi.com/v3.1"


def load_fixture(name: str) -> Any:
    """Load ``tests/fixtures/<name>`` (``.json`` may be omitted)."""

    filename = name if name.endswith(".json") else f"{name}.json"
    path = FIXTURES_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"Fixture {filename!r} not found in {FIXTURES_DIR}. "
            "Fixtures are extracted from the backend routers (see fixtures/SOURCES.md)."
        )
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture
def fixture() -> Any:
    """Fixture loader as a fixture, so tests can do ``fixture("weather_metar")``."""

    return load_fixture


class SleepRecorder:
    """Drop-in replacement for ``time.sleep`` that records instead of sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)

    @property
    def count(self) -> int:
        return len(self.calls)


class AsyncSleepRecorder:
    """Awaitable variant of :class:`SleepRecorder`."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)

    @property
    def count(self) -> int:
        return len(self.calls)


@pytest.fixture
def sleeper() -> SleepRecorder:
    return SleepRecorder()


@pytest.fixture
def async_sleeper() -> AsyncSleepRecorder:
    return AsyncSleepRecorder()


@pytest.fixture
def client(sleeper: SleepRecorder) -> Iterator[SkyLink]:
    with SkyLink(api_key=TEST_API_KEY, provider=TEST_PROVIDER, sleep=sleeper, environ={}) as sky:
        yield sky


@pytest.fixture
async def async_client(async_sleeper: AsyncSleepRecorder) -> Any:
    async with AsyncSkyLink(
        api_key=TEST_API_KEY, provider=TEST_PROVIDER, sleep=async_sleeper, environ={}
    ) as sky:
        yield sky
