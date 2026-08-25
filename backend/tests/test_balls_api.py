"""`GET /api/v1/balls` -- the published ball catalog.

The point of this endpoint is that a client stops hardcoding legal
`ball_id` values, so these tests pin the two things that would quietly
break that: the published list drifting from `BALL_CATALOG`, and the
published list drifting from what a throw actually accepts.
"""

import copy
from dataclasses import asdict
from typing import get_args

from fastapi.testclient import TestClient

from app.api.routes.balls import _COVERSTOCK_CHARACTER
from app.main import app
from app.models.schemas import BallResponse
from app.physics.ball import BALL_CATALOG, Coverstock

client = TestClient(app)

# The release the game route already validates against; only `ball_id`
# varies across the throw checks below.
THROW_PAYLOAD = {
    "speed_mph": 17.0,
    "rev_rate": 350.0,
    "axis_rotation": 45.0,
    "axis_tilt": 15.0,
    "launch_angle": -1.5,
    "launch_position": 28.0,
}


def _catalog() -> list:
    response = client.get("/api/v1/balls")
    assert response.status_code == 200
    return response.json()["balls"]


def _new_game_id() -> str:
    response = client.post("/api/v1/games", json={})
    assert response.status_code in (200, 201), response.text
    return response.json()["game_id"]


def test_returns_every_catalog_id_in_declared_order():
    """Order is part of the contract: the client renders the selector in
    the order this returns, so a reordered catalog is a visible change."""
    assert [ball["id"] for ball in _catalog()] == list(BALL_CATALOG)


def test_includes_the_default_reactive_pearl():
    by_id = {ball["id"]: ball for ball in _catalog()}

    assert "reactive_pearl" in by_id
    assert by_id["reactive_pearl"]["name"] == "Reactive Pearl"
    assert by_id["reactive_pearl"]["coverstock"] == "reactive"


def test_ids_are_unique():
    ids = [ball["id"] for ball in _catalog()]

    assert len(ids) == len(set(ids))


def test_coverstock_serializes_as_the_plain_value():
    """`Coverstock` is a `str`/`Enum` mixin whose `str()` is
    `'Coverstock.REACTIVE'`. Publishing that repr instead of `'reactive'`
    is the specific regression this catches."""
    published = {ball["coverstock"] for ball in _catalog()}

    assert published <= {"plastic", "urethane", "reactive", "particle"}
    for value in published:
        assert "Coverstock." not in value
        assert value == value.lower()

    # And the values really are the catalog's own, not a coincidence.
    assert [ball["coverstock"] for ball in _catalog()] == [
        ball.coverstock.value for ball in BALL_CATALOG.values()
    ]


def test_every_field_matches_the_catalog_entry_it_came_from():
    published_balls = _catalog()
    catalog_balls = list(BALL_CATALOG.values())
    assert len(published_balls) == len(catalog_balls)

    # Explicit indexing rather than zip(..., strict=): the runtime floor
    # is Python 3.9, and an unequal-length zip would silently pass.
    for index in range(len(catalog_balls)):
        published = published_balls[index]
        ball = catalog_balls[index]
        assert published["id"] == ball.id
        assert published["name"] == ball.name
        assert published["surface"] == ball.surface
        assert published["spec"]["mass_lbs"] == ball.mass_lbs
        assert published["spec"]["radius_in"] == ball.radius_in
        assert published["spec"]["rg_in"] == ball.rg_in
        assert published["spec"]["differential"] == ball.differential
        assert published["spec"]["hook_potential"] == ball.hook_potential


def test_every_ball_has_usable_display_text():
    """The frontend renders `description` for a ball whose id it has
    never seen, so an empty or id-shaped string would be a real defect."""
    for published in _catalog():
        description = published["description"]
        assert description.strip() == description
        assert len(description) > 20
        assert published["coverstock"] in description.lower()
        assert published["surface"] in description


def test_every_coverstock_has_display_text():
    """A new `Coverstock` member without a description entry would raise
    on the first request rather than publish an empty string. This fails
    at the point the member is added instead."""
    assert set(_COVERSTOCK_CHARACTER) == set(Coverstock)


def test_the_response_schema_accepts_exactly_the_declared_coverstocks():
    """The `Literal` in `BallResponse` and the `Coverstock` enum have to
    stay in step; otherwise a legitimately added coverstock would fail
    serialization."""
    declared = get_args(BallResponse.model_fields["coverstock"].annotation)

    assert set(declared) == {member.value for member in Coverstock}


def test_serving_the_catalog_does_not_mutate_it():
    snapshot = copy.deepcopy({key: asdict(ball) for key, ball in BALL_CATALOG.items()})

    _catalog()

    assert {key: asdict(ball) for key, ball in BALL_CATALOG.items()} == snapshot
    assert list(BALL_CATALOG) == list(snapshot)


def test_the_catalog_is_deterministic_across_calls():
    assert _catalog() == _catalog()


def test_every_published_ball_is_accepted_by_a_throw():
    """The published catalog and throw validation read the same dict, so
    every id here must be throwable. This is the check that would catch a
    catalog that published something the game route rejects."""
    for published in _catalog():
        game_id = _new_game_id()
        response = client.post(
            f"/api/v1/games/{game_id}/throws",
            json={**THROW_PAYLOAD, "ball_id": published["id"], "seed": 17},
        )

        assert response.status_code == 200, (published["id"], response.text)


def test_a_ball_absent_from_the_catalog_is_still_rejected():
    """The existing 404 contract for an unknown `ball_id`, kept intact:
    publishing the catalog must not turn into accepting anything."""
    unknown = "not_a_real_ball"
    assert unknown not in {ball["id"] for ball in _catalog()}

    game_id = _new_game_id()
    response = client.post(
        f"/api/v1/games/{game_id}/throws",
        json={**THROW_PAYLOAD, "ball_id": unknown, "seed": 17},
    )

    assert response.status_code == 404


def test_the_catalog_endpoint_is_read_only():
    assert client.post("/api/v1/balls", json={}).status_code == 405
    assert client.delete("/api/v1/balls").status_code == 405
