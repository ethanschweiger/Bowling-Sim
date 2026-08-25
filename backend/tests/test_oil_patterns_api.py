"""`GET /api/v1/oil-patterns` -- the published oil pattern catalog.

The point of this endpoint is that a client stops hardcoding legal
`oil_pattern` values, so these tests pin the two things that would
quietly break that: the published list drifting from
`SUPPORTED_OIL_PATTERNS`, and the published list drifting from what
`POST /api/v1/games` actually accepts.
"""

import copy
from dataclasses import asdict

from fastapi.testclient import TestClient

from app.api.routes.oil_patterns import _PATTERN_DESCRIPTION
from app.games.service import SUPPORTED_OIL_PATTERNS
from app.main import app
from app.physics.lane import HOUSE_SHOT_SPEC, LaneCondition

client = TestClient(app)


def _catalog() -> list:
    response = client.get("/api/v1/oil-patterns")
    assert response.status_code == 200
    return response.json()["patterns"]


def test_returns_every_supported_id_in_declared_order():
    """Order is part of the contract: a client renders the catalog in the
    order this returns, so a reordered registry is a visible change."""
    assert [pattern["id"] for pattern in _catalog()] == list(SUPPORTED_OIL_PATTERNS)


def test_includes_house():
    by_id = {pattern["id"]: pattern for pattern in _catalog()}

    assert "house" in by_id
    assert by_id["house"]["name"] == "House Shot"


def test_ids_are_unique():
    ids = [pattern["id"] for pattern in _catalog()]

    assert len(ids) == len(set(ids))


def test_the_house_spec_matches_house_shot_spec_exactly():
    """The published spec is read from the same builder
    `POST /api/v1/games` uses, not a hand-copied second source -- this
    pins every field against the actual constant."""
    published = next(pattern for pattern in _catalog() if pattern["id"] == "house")["spec"]

    assert published["length_ft"] == HOUSE_SHOT_SPEC.length_ft
    assert published["taper_ft"] == HOUSE_SHOT_SPEC.taper_ft
    assert tuple(published["center_boards"]) == HOUSE_SHOT_SPEC.center_boards
    assert tuple(published["total_boards"]) == HOUSE_SHOT_SPEC.total_boards
    assert published["pattern_ratio"] == HOUSE_SHOT_SPEC.pattern_ratio
    assert published["total_volume_ml"] == HOUSE_SHOT_SPEC.total_volume_ml


def test_every_pattern_has_usable_display_text():
    for published in _catalog():
        description = published["description"]
        assert description.strip() == description
        assert len(description) > 20


def test_every_supported_pattern_has_display_text():
    """A new registry entry without a description entry would raise on
    the first request rather than publish an empty string. This fails at
    the point the entry is added instead."""
    assert set(_PATTERN_DESCRIPTION) == set(SUPPORTED_OIL_PATTERNS)


def test_serving_the_catalog_does_not_mutate_the_registry_or_spec():
    registry_before = list(SUPPORTED_OIL_PATTERNS.items())
    spec_before = copy.deepcopy(asdict(HOUSE_SHOT_SPEC))

    _catalog()

    assert list(SUPPORTED_OIL_PATTERNS.items()) == registry_before
    assert asdict(HOUSE_SHOT_SPEC) == spec_before


def test_the_catalog_is_deterministic_across_calls():
    assert _catalog() == _catalog()


def test_every_published_pattern_id_creates_a_game():
    """The published catalog and game creation read the same registry, so
    every id here must actually be creatable."""
    for published in _catalog():
        response = client.post("/api/v1/games", json={"oil_pattern": published["id"]})

        assert response.status_code == 201, (published["id"], response.text)


def test_unsupported_oil_pattern_is_still_rejected():
    """The existing 422 contract for an unsupported `oil_pattern`, kept
    intact: publishing the catalog must not turn into accepting anything."""
    unsupported = "not_a_real_pattern"
    assert unsupported not in {pattern["id"] for pattern in _catalog()}

    response = client.post("/api/v1/games", json={"oil_pattern": unsupported})

    assert response.status_code == 422


def test_omitting_oil_pattern_still_defaults_to_house():
    response = client.post("/api/v1/games", json={})

    assert response.status_code == 201


def test_the_registry_still_has_exactly_one_pattern():
    """Documents the current scope directly: this milestone publishes the
    catalog, it does not add a second pattern. A registry addition should
    make this fail loudly rather than pass unnoticed."""
    assert list(SUPPORTED_OIL_PATTERNS) == ["house"]


def test_the_catalog_endpoint_is_read_only():
    assert client.post("/api/v1/oil-patterns", json={}).status_code == 405
    assert client.delete("/api/v1/oil-patterns").status_code == 405


def test_calling_the_registered_builder_does_not_affect_a_real_game():
    """The endpoint calls each registry builder to read its spec. Each
    call constructs a brand-new `LaneCondition`, so this cannot leak state
    into a `LaneCondition` a real game is already using."""
    lane_before = LaneCondition.house_shot()

    _catalog()

    lane_after = LaneCondition.house_shot()
    assert lane_before.oil_grid == lane_after.oil_grid
    assert lane_before.peak_oil_ml == lane_after.peak_oil_ml
