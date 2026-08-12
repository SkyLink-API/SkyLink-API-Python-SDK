"""``sky.geo`` — country and region reference data.

Payloads are built inline from ``models/v3/countries.py`` and
``models/v3/regions.py`` (neither route has an OpenAPI example block). The point
of interest is the list-vs-detail shape split: an envelope on one side, a flat
record on the other.

The namespace is not attached to the client yet (task A8 does the wiring), so
the resource classes are instantiated directly here.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import TEST_BASE_URL
from skylink_api._client import AsyncSkyLink, SkyLink
from skylink_api._exceptions import BadRequestError, NotFoundError
from skylink_api.models.geo import (
    CountriesResponse,
    Country,
    CountryDetail,
    Region,
    RegionDetail,
    RegionsResponse,
)
from skylink_api.resources.geo import (
    AsyncGeo,
    Geo,
    _countries_spec,
    _country_spec,
    _region_spec,
    _regions_spec,
)

US: dict[str, Any] = {
    "id": 302791,
    "code": "US",
    "name": "United States",
    "continent": "NA",
    "wikipedia_link": "https://en.wikipedia.org/wiki/United_States",
    "keywords": None,
}

GB: dict[str, Any] = {
    "id": 302672,
    "code": "GB",
    "name": "United Kingdom",
    "continent": "EU",
    "wikipedia_link": "https://en.wikipedia.org/wiki/United_Kingdom",
    "keywords": "Great Britain",
}

US_NY: dict[str, Any] = {
    "id": 306091,
    "code": "US-NY",
    "local_code": "NY",
    "name": "New York",
    "continent": "NA",
    "iso_country": "US",
    "wikipedia_link": "https://en.wikipedia.org/wiki/New_York_(state)",
    "keywords": None,
}

US_CA: dict[str, Any] = {
    "id": 306034,
    "code": "US-CA",
    "local_code": "CA",
    "name": "California",
    "continent": "NA",
    "iso_country": "US",
    "wikipedia_link": "https://en.wikipedia.org/wiki/California",
    "keywords": None,
}


@pytest.fixture
def geo(client: SkyLink) -> Geo:
    return Geo(client)


@pytest.fixture
def async_geo(async_client: AsyncSkyLink) -> AsyncGeo:
    return AsyncGeo(async_client)


def _mock(respx_mock: respx.MockRouter, path: str, payload: Any) -> respx.Route:
    return respx_mock.get(url__startswith=f"{TEST_BASE_URL}{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── builders (no I/O) ────────────────────────────────────────────────────────


def test_builders_produce_the_documented_specs() -> None:
    assert _countries_spec().path == "/countries"
    assert _countries_spec().query == {"continent": None}
    assert _countries_spec(continent="EU").query == {"continent": "EU"}
    assert _countries_spec().cast_to is CountriesResponse

    # Detail routes take no query at all and cast to the flat model.
    assert _country_spec("US").path == "/countries/US"
    assert _country_spec("US").query is None
    assert _country_spec("US").cast_to is CountryDetail

    assert _regions_spec().path == "/regions"
    assert _regions_spec(country="US", continent="NA").query == {
        "country": "US",
        "continent": "NA",
    }
    assert _regions_spec().cast_to is RegionsResponse

    assert _region_spec("US-CA").path == "/regions/US-CA"
    assert _region_spec("US-CA").cast_to is RegionDetail

    assert {
        spec.method
        for spec in (_countries_spec(), _country_spec("US"), _regions_spec(), _region_spec("US-CA"))
    } == {"GET"}


# ── countries ────────────────────────────────────────────────────────────────


def test_countries_unfiltered(geo: Geo, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/countries", {"countries": [US, GB], "total": 2})

    result = geo.countries()

    request = route.calls.last.request
    assert request.url.path == "/v3.1/countries"
    assert "continent" not in request.url.params
    assert request.headers["x-api-key"] == "test-key"

    assert isinstance(result, CountriesResponse)
    assert result.total == 2
    assert isinstance(result.countries[0], Country)
    assert [c.code for c in result.countries] == ["US", "GB"]
    assert result.countries[0].name == "United States"
    assert result.countries[0].keywords is None
    assert result.countries[1].continent == "EU"


def test_countries_by_continent(geo: Geo, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/countries", {"countries": [GB], "total": 1})

    result = geo.countries(continent="EU")

    assert route.calls.last.request.url.params["continent"] == "EU"
    assert [c.code for c in result.countries] == ["GB"]


def test_countries_bad_continent_is_a_400(geo: Geo, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/countries").mock(
        return_value=httpx.Response(400, json={"detail": "Invalid continent 'XX'."})
    )

    with pytest.raises(BadRequestError) as excinfo:
        # An invalid literal is a type error, but the runtime still handles it.
        geo.countries(continent="XX")  # type: ignore[arg-type]

    assert excinfo.value.status_code == 400


def test_country_detail_is_flat_not_wrapped(geo: Geo, respx_mock: respx.MockRouter) -> None:
    """The detail route answers with the record itself — no ``{countries, total}``."""

    route = _mock(respx_mock, "/countries/US", US)

    country = geo.country("US")

    assert route.calls.last.request.url.path == "/v3.1/countries/US"
    assert isinstance(country, CountryDetail)
    assert not isinstance(country, CountriesResponse)
    assert country.code == "US"
    assert country.name == "United States"
    assert country.continent == "NA"


def test_country_detail_lowercase_code_and_unknown_fields(
    geo: Geo, respx_mock: respx.MockRouter
) -> None:
    route = _mock(respx_mock, "/countries/gb", {**GB, "brand_new_field": 1})

    country = geo.country("gb")

    # The path is used verbatim; the API upper-cases the code itself.
    assert route.calls.last.request.url.path == "/v3.1/countries/gb"
    assert country.code == "GB"
    assert country.model_extra is not None
    assert country.model_extra["brand_new_field"] == 1


def test_country_detail_404(geo: Geo, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/countries/ZZ").mock(
        return_value=httpx.Response(404, json={"detail": "Country 'ZZ' not found"})
    )

    with pytest.raises(NotFoundError) as excinfo:
        geo.country("ZZ")

    assert excinfo.value.status_code == 404
    assert "ZZ" in str(excinfo.value)


# ── regions ──────────────────────────────────────────────────────────────────


def test_regions_by_country(geo: Geo, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/regions", {"regions": [US_CA, US_NY], "total": 2})

    result = geo.regions(country="US")

    request = route.calls.last.request
    assert request.url.path == "/v3.1/regions"
    assert request.url.params["country"] == "US"
    assert "continent" not in request.url.params

    assert isinstance(result, RegionsResponse)
    assert result.total == 2
    assert isinstance(result.regions[0], Region)
    assert [r.code for r in result.regions] == ["US-CA", "US-NY"]
    assert result.regions[0].local_code == "CA"
    assert result.regions[0].iso_country == "US"


def test_regions_both_filters_combine(geo: Geo, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/regions", {"regions": [], "total": 0})

    result = geo.regions(country="US", continent="EU")

    params = route.calls.last.request.url.params
    assert params["country"] == "US"
    assert params["continent"] == "EU"
    # Contradictory filters give an empty envelope, not a 404.
    assert result.regions == []
    assert result.total == 0


def test_regions_unfiltered(geo: Geo, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/regions", {"regions": [US_NY], "total": 1})

    geo.regions()

    params = route.calls.last.request.url.params
    assert "country" not in params
    assert "continent" not in params


def test_region_detail_is_flat_not_wrapped(geo: Geo, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/regions/US-CA", US_CA)

    region = geo.region("US-CA")

    assert route.calls.last.request.url.path == "/v3.1/regions/US-CA"
    assert isinstance(region, RegionDetail)
    assert not isinstance(region, RegionsResponse)
    assert region.code == "US-CA"
    assert region.local_code == "CA"
    assert region.name == "California"
    assert region.iso_country == "US"


def test_region_detail_404(geo: Geo, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__startswith=f"{TEST_BASE_URL}/regions/ZZ-ZZ").mock(
        return_value=httpx.Response(404, json={"detail": "Region 'ZZ-ZZ' not found"})
    )

    with pytest.raises(NotFoundError):
        geo.region("ZZ-ZZ")


def test_request_options_are_forwarded(geo: Geo, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/countries", {"countries": [US], "total": 1})

    geo.countries(request_options={"headers": {"X-Trace": "abc"}, "query": {"debug": True}})

    request = route.calls.last.request
    assert request.headers["X-Trace"] == "abc"
    assert request.url.params["debug"] == "true"


# ── async ────────────────────────────────────────────────────────────────────


async def test_async_countries(async_geo: AsyncGeo, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/countries", {"countries": [GB], "total": 1})

    result = await async_geo.countries(continent="EU")

    assert route.calls.last.request.url.params["continent"] == "EU"
    assert result.countries[0].code == "GB"


async def test_async_country_and_region_detail(
    async_geo: AsyncGeo, respx_mock: respx.MockRouter
) -> None:
    _mock(respx_mock, "/countries/US", US)
    _mock(respx_mock, "/regions/US-NY", US_NY)

    country = await async_geo.country("US")
    region = await async_geo.region("US-NY")

    assert isinstance(country, CountryDetail)
    assert country.name == "United States"
    assert isinstance(region, RegionDetail)
    assert region.local_code == "NY"


async def test_async_regions(async_geo: AsyncGeo, respx_mock: respx.MockRouter) -> None:
    route = _mock(respx_mock, "/regions", {"regions": [US_CA], "total": 1})

    result = await async_geo.regions(country="US", continent="NA")

    params = route.calls.last.request.url.params
    assert params["country"] == "US"
    assert params["continent"] == "NA"
    assert result.regions[0].code == "US-CA"
