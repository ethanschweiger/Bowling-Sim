from fastapi.testclient import TestClient

from app.api.dependencies import get_game_service
from app.api.routes.games import TRUNCATED_TRAJECTORY_DETAIL
from app.games.service import (
    GameService,
    GameSessionRepository,
    InMemoryGameSessionRepository,
    default_game_service,
)
from app.main import app
from app.physics.ball import BALL_CATALOG
from app.physics.lane import CHALLENGE_PATTERN_SPEC, HOUSE_SHOT_SPEC
from app.physics.pinfall import PinfallResult
from app.physics.simulate import SimulationResult, TerminalState, TrajectoryPoint, simulate_throw
from app.physics.throw import Throw

client = TestClient(app)

THROW_PAYLOAD = {
    "ball_id": "reactive_pearl",
    "speed_mph": 17.0,
    "rev_rate": 350.0,
    "axis_rotation": 45.0,
    "axis_tilt": 15.0,
    "launch_angle": 0.5,
    "launch_position": 28.0,
}


def _create_game():
    response = client.post("/api/v1/games", json={})
    assert response.status_code == 201
    return response.json()


def test_create_game_returns_id_and_initial_version():
    body = _create_game()
    assert isinstance(body["game_id"], str) and body["game_id"]
    assert body["lane_condition_version"] == 1


def test_unsupported_oil_pattern_is_rejected():
    response = client.post("/api/v1/games", json={"oil_pattern": "sport"})
    assert response.status_code == 422  # not yet a supported pattern


def test_omitted_oil_pattern_defaults_to_house():
    body = _create_game()
    session = default_game_service.get_game(body["game_id"])
    assert session.lane.condition.spec.name == "House Shot"


def test_challenge_oil_pattern_is_accepted_and_builds_the_challenge_lane_condition():
    response = client.post("/api/v1/games", json={"oil_pattern": "challenge"})
    assert response.status_code == 201
    body = response.json()
    assert body["lane_condition_version"] == 1

    session = default_game_service.get_game(body["game_id"])
    assert session.lane.condition.spec.name == "Challenge Pattern"
    assert session.lane.condition.spec == CHALLENGE_PATTERN_SPEC
    # A genuinely different pattern, not the house shot under a new id.
    assert session.lane.condition.spec != HOUSE_SHOT_SPEC


def test_created_game_state_reports_the_house_default_oil_pattern():
    body = _create_game()
    assert body["game_state"]["oil_pattern"] == "house"


def test_challenge_oil_pattern_is_reported_in_game_state_on_every_read():
    """The registry id a game was created with must come back out of
    `game_state.oil_pattern` -- not only from the create response `Codex`
    could already see, but from every later read of the same game: GET, a
    throw, and a reset alike. Regression this guards: an earlier version
    of this feature would have reported it on create and silently omitted
    it (or reported the wrong pattern) everywhere else."""
    create_response = client.post("/api/v1/games", json={"oil_pattern": "challenge"})
    assert create_response.status_code == 201
    game = create_response.json()
    game_id = game["game_id"]
    assert game["game_state"]["oil_pattern"] == "challenge"

    get_body = client.get(f"/api/v1/games/{game_id}").json()
    assert get_body["game_state"]["oil_pattern"] == "challenge"

    throw_body = client.post(
        f"/api/v1/games/{game_id}/throws", json={**THROW_PAYLOAD, "seed": 3}
    ).json()
    assert throw_body["game_state"]["oil_pattern"] == "challenge"

    reset_body = client.post(f"/api/v1/games/{game_id}/reset").json()
    assert reset_body["game_state"]["oil_pattern"] == "challenge"

    # reset() returns to the same pattern the game was created with, not
    # back to the house default.
    after_reset_get = client.get(f"/api/v1/games/{game_id}").json()
    assert after_reset_get["game_state"]["oil_pattern"] == "challenge"


def test_unknown_game_id_returns_404_for_throws_and_reset():
    throw_response = client.post("/api/v1/games/does-not-exist/throws", json=THROW_PAYLOAD)
    assert throw_response.status_code == 404

    reset_response = client.post("/api/v1/games/does-not-exist/reset")
    assert reset_response.status_code == 404


def _truncated_simulate_throw(ball, throw, lane_condition, step_ft=None):
    """A `simulate_throw`-shaped stand-in whose result never reaches the pin
    deck — deterministic, no monkeypatched global stride, no need to hunt
    for a real release that happens to fail."""
    return SimulationResult(
        path=[TrajectoryPoint(distance_ft=0.0, board=throw.launch_position)],
        entry_board=throw.launch_position,
        entry_angle_deg=0.0,
        speed_at_pins_mph=10.0,
        lane_condition_version=lane_condition.version,
        terminal=TerminalState(
            distance_ft=10.0,
            board=throw.launch_position,
            heading_deg=0.0,
            speed_mph=10.0,
            reached_pin_deck=False,
        ),
    )


def test_truncated_trajectory_returns_503_and_leaves_game_state_unchanged(monkeypatch):
    game = _create_game()
    game_id = game["game_id"]
    before = client.get(f"/api/v1/games/{game_id}").json()

    # Patch the name the route actually calls, not the module it's defined
    # in — `create_game_throw` resolves `simulate_throw` from
    # `app.api.routes.games`'s own globals at call time.
    monkeypatch.setattr("app.api.routes.games.simulate_throw", _truncated_simulate_throw)

    response = client.post(f"/api/v1/games/{game_id}/throws", json=THROW_PAYLOAD)

    assert response.status_code == 503
    body = response.json()
    # Exact match, not a substring check: proves nothing else — no stack
    # trace, no raw distance/stride figure from the domain exception's own
    # message — was appended to the stable text.
    assert body["detail"] == TRUNCATED_TRAJECTORY_DETAIL

    after = client.get(f"/api/v1/games/{game_id}").json()
    assert after == before, "a rejected throw must not change lane version, rack, or scorecard"


def test_truncated_trajectory_does_not_advance_the_scorecard_across_repeated_calls(monkeypatch):
    """Two rejected throws in a row must both be no-ops, not merely the first."""
    game = _create_game()
    game_id = game["game_id"]
    monkeypatch.setattr("app.api.routes.games.simulate_throw", _truncated_simulate_throw)

    first = client.post(f"/api/v1/games/{game_id}/throws", json=THROW_PAYLOAD)
    second = client.post(f"/api/v1/games/{game_id}/throws", json=THROW_PAYLOAD)
    assert first.status_code == second.status_code == 503

    status = client.get(f"/api/v1/games/{game_id}").json()
    assert status["lane_condition_version"] == 1
    assert status["game_state"]["frames"] == []
    assert status["game_state"]["total_score"] is None


def test_two_games_wear_independently_through_the_api():
    game_a = _create_game()
    game_b = _create_game()

    first = client.post(
        f"/api/v1/games/{game_a['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 1}
    ).json()
    assert first["game_id"] == game_a["game_id"]
    assert first["lane_condition_version"] == 1

    second_a = client.post(
        f"/api/v1/games/{game_a['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 2}
    ).json()
    assert second_a["lane_condition_version"] == 2

    # game_b never had a throw — still at its initial version.
    reset_b = client.post(f"/api/v1/games/{game_b['game_id']}/reset").json()
    assert reset_b["lane_condition_version"] == 1


def test_reset_returns_game_to_version_1():
    game = _create_game()
    client.post(f"/api/v1/games/{game['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 1})
    client.post(f"/api/v1/games/{game['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 2})

    reset_body = client.post(f"/api/v1/games/{game['game_id']}/reset").json()
    assert reset_body["game_id"] == game["game_id"]
    assert reset_body["lane_condition_version"] == 1

    after_reset = client.post(
        f"/api/v1/games/{game['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 1}
    ).json()
    assert after_reset["lane_condition_version"] == 1


def test_game_throw_response_uses_the_planar_collision_model():
    game = _create_game()
    body = client.post(
        f"/api/v1/games/{game['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 7}
    ).json()

    assert body["pinfall"]["model_id"] == "planar-collision-2d-v1"
    assert isinstance(body["pinfall"]["fallen_pin_ids"], list)
    assert all(1 <= pin_id <= 10 for pin_id in body["pinfall"]["fallen_pin_ids"])
    assert len(set(body["pinfall"]["fallen_pin_ids"])) == len(body["pinfall"]["fallen_pin_ids"])
    assert body["pins_knocked"] == len(body["pinfall"]["fallen_pin_ids"])


def test_create_game_state_is_a_fresh_blank_scorecard_and_full_rack():
    body = _create_game()
    gs = body["game_state"]
    assert gs["standing_pin_ids"] == list(range(1, 11))
    assert gs["frames"] == []
    assert gs["total_score"] is None
    assert gs["is_game_complete"] is False
    assert gs["next_frame_number"] == 1
    assert gs["next_ball_number"] == 1


def test_throw_response_game_state_is_internally_consistent():
    game = _create_game()
    body = client.post(
        f"/api/v1/games/{game['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 3}
    ).json()
    gs = body["game_state"]

    assert len(gs["frames"]) >= 1
    frame1 = gs["frames"][0]
    if frame1["is_complete"]:
        # Either a strike, or frame 1 finished on ball 2 — either way the
        # next ball (frame 2 or the tenth-frame equivalent) gets a fresh rack.
        assert len(gs["standing_pin_ids"]) == 10
    else:
        # Frame 1 still waiting on ball 2 — the rack reflects exactly what
        # ball 1 knocked down.
        assert len(gs["standing_pin_ids"]) == 10 - body["pins_knocked"]
    assert body["pins_knocked"] == sum(frame1["rolls"])


def test_reset_response_game_state_matches_a_fresh_game():
    game = _create_game()
    client.post(f"/api/v1/games/{game['game_id']}/throws", json={**THROW_PAYLOAD, "seed": 1})

    reset_body = client.post(f"/api/v1/games/{game['game_id']}/reset").json()
    gs = reset_body["game_state"]
    assert gs["standing_pin_ids"] == list(range(1, 11))
    assert gs["frames"] == []
    assert gs["is_game_complete"] is False
    assert reset_body["lane_condition_version"] == 1


def test_throw_after_game_completion_returns_409_and_reset_recovers():
    game = _create_game()
    game_id = game["game_id"]
    session = default_game_service.get_game(game_id)
    ball = BALL_CATALOG["house_ball"]
    throw = Throw()

    def strike_everything(condition):
        return simulate_throw(ball, throw, condition)

    def resolve_strike(sim_result, standing_ids):
        return PinfallResult(
            pins_knocked=10,
            model_id="t",
            limitations="",
            fallen_pin_ids=tuple(sorted(standing_ids)),
        )

    for _ in range(12):
        session.throw(simulate=strike_everything, resolve_pinfall=resolve_strike)
    assert session.current_snapshot().is_game_complete

    lane_version_before = session.lane.condition.version

    response = client.post(f"/api/v1/games/{game_id}/throws", json=THROW_PAYLOAD)
    assert response.status_code == 409
    assert session.lane.condition.version == lane_version_before  # rejected before anything changed

    reset_body = client.post(f"/api/v1/games/{game_id}/reset").json()
    assert reset_body["lane_condition_version"] == 1
    assert reset_body["game_state"]["is_game_complete"] is False
    assert reset_body["game_state"]["total_score"] is None

    # The game accepts throws again after reset.
    after_reset = client.post(f"/api/v1/games/{game_id}/throws", json={**THROW_PAYLOAD, "seed": 1})
    assert after_reset.status_code == 200


def _force_roll(session, pins_knocked):
    """Forces one roll's exact pinfall count through a real `GameSession`,
    the same `simulate`/`resolve_pinfall` injection
    `test_throw_after_game_completion_returns_409_and_reset_recovers` uses
    -- lets these tests drive the scorecard into an exact frame/roll shape
    without hunting for a real release that happens to land it."""
    ball = BALL_CATALOG["house_ball"]
    throw = Throw()

    def simulate(condition):
        return simulate_throw(ball, throw, condition)

    def resolve(_sim_result, standing_ids):
        fallen = tuple(sorted(standing_ids)[:pins_knocked])
        return PinfallResult(
            pins_knocked=pins_knocked,
            model_id="t",
            limitations="",
            fallen_pin_ids=fallen,
        )

    return session.throw(simulate=simulate, resolve_pinfall=resolve)


def test_tenth_frame_double_strike_bonus_reports_x_not_a_plain_count():
    """The motivating README case: a bonus ball on a fresh rack after an
    opening tenth-frame strike must report `X`, not the raw pin count.
    Before this feature the frontend derived glyphs from
    `is_strike`/`is_spare` alone, which can't distinguish a second
    fresh-rack strike from an ordinary roll — this is exactly the gap
    the server-owned symbols close."""
    game = _create_game()
    game_id = game["game_id"]
    session = default_game_service.get_game(game_id)

    for _ in range(9):
        _force_roll(session, 10)  # frames 1-9: all strikes
    _force_roll(session, 10)  # frame 10, ball 1: strike
    _force_roll(session, 10)  # frame 10, ball 2: strike on the fresh bonus rack
    _force_roll(session, 4)  # frame 10, ball 3: an ordinary roll on its own fresh rack

    status = client.get(f"/api/v1/games/{game_id}").json()
    tenth = status["game_state"]["frames"][9]
    assert tenth["rolls"] == [10, 10, 4]
    assert tenth["roll_symbols"] == ["X", "X", "4"]


def test_frame_symbols_reflect_strike_spare_open_and_miss_via_the_api():
    game = _create_game()
    game_id = game["game_id"]
    session = default_game_service.get_game(game_id)

    _force_roll(session, 10)  # frame 1: strike
    _force_roll(session, 6)  # frame 2, ball 1
    _force_roll(session, 4)  # frame 2, ball 2: spare (6 + 4)
    _force_roll(session, 3)  # frame 3, ball 1
    _force_roll(session, 4)  # frame 3, ball 2: open (3 + 4)
    _force_roll(session, 0)  # frame 4, ball 1: miss
    _force_roll(session, 0)  # frame 4, ball 2: miss

    frames = client.get(f"/api/v1/games/{game_id}").json()["game_state"]["frames"]
    assert frames[0]["roll_symbols"] == ["X"]
    assert frames[1]["roll_symbols"] == ["6", "/"]
    assert frames[2]["roll_symbols"] == ["3", "4"]
    assert frames[3]["roll_symbols"] == ["-", "-"]


def test_roll_symbols_reset_cleanly_and_stay_consistent_across_get():
    game = _create_game()
    game_id = game["game_id"]
    throw_body = client.post(
        f"/api/v1/games/{game_id}/throws", json={**THROW_PAYLOAD, "seed": 3}
    ).json()
    # A real throw always populates one symbol per roll in every frame it
    # touches — the field is never silently absent from a real response.
    for frame in throw_body["game_state"]["frames"]:
        assert len(frame["roll_symbols"]) == len(frame["rolls"])

    reset_body = client.post(f"/api/v1/games/{game_id}/reset").json()
    assert reset_body["game_state"]["frames"] == []

    status = client.get(f"/api/v1/games/{game_id}").json()
    assert status["game_state"] == reset_body["game_state"]


def test_get_unknown_game_returns_404():
    response = client.get("/api/v1/games/does-not-exist")
    assert response.status_code == 404


def test_get_fresh_game_matches_create_response():
    created = _create_game()
    game_id = created["game_id"]

    status = client.get(f"/api/v1/games/{game_id}").json()
    assert status["game_id"] == game_id
    assert status["lane_condition_version"] == created["lane_condition_version"]
    assert status["game_state"] == created["game_state"]


def test_get_after_a_throw_matches_the_throws_own_game_state():
    game = _create_game()
    game_id = game["game_id"]

    throw_body = client.post(
        f"/api/v1/games/{game_id}/throws", json={**THROW_PAYLOAD, "seed": 5}
    ).json()
    status = client.get(f"/api/v1/games/{game_id}").json()

    # The throw response's lane_condition_version is (unchanged, documented
    # semantic) the version that throw ran *against* — GET reports the
    # current version, i.e. one *after* wear from that same throw applied.
    assert status["lane_condition_version"] == throw_body["lane_condition_version"] + 1
    # game_state itself (frames/rack/score) describes the same committed
    # state the throw's own response already reported — nothing else
    # touched this game between the two calls.
    assert status["game_state"] == throw_body["game_state"]


def test_get_after_reset_matches_a_fresh_game():
    game = _create_game()
    game_id = game["game_id"]
    client.post(f"/api/v1/games/{game_id}/throws", json={**THROW_PAYLOAD, "seed": 2})

    reset_body = client.post(f"/api/v1/games/{game_id}/reset").json()
    status = client.get(f"/api/v1/games/{game_id}").json()

    assert status["lane_condition_version"] == reset_body["lane_condition_version"] == 1
    assert status["game_state"] == reset_body["game_state"]
    assert status["game_state"]["standing_pin_ids"] == list(range(1, 11))
    assert status["game_state"]["frames"] == []


class _SpyRepository(GameSessionRepository):
    """See tests/test_game_service_writeback.py's identical helper --
    wraps a real repository, delegating every call, while recording each
    `game_id` a `put()` call was made for."""

    def __init__(self, inner):
        self._inner = inner
        self.put_calls = []

    def get(self, game_id):
        return self._inner.get(game_id)

    def put(self, session):
        self.put_calls.append(session.game_id)
        self._inner.put(session)

    def get_or_put(self, game_id, factory):
        return self._inner.get_or_put(game_id, factory)


def test_game_scoped_throw_and_reset_write_back_through_the_service(monkeypatch):
    """The API-level proof that create_game_throw/reset_game route
    through GameService.throw_in_game/reset_game (and therefore a
    repository write-back), not a direct session.throw()/reset() call
    that would silently skip it for a future persistent repository.
    Exercises a temporary spy-wrapped GameService substituted through
    the get_game_service FastAPI dependency for this test only --
    default_game_service (used by the rest of the suite) is never
    touched. monkeypatch.setitem cleans the override up automatically
    at teardown, the same as it would any other dict entry."""
    spy = _SpyRepository(InMemoryGameSessionRepository())
    temp_service = GameService(repository=spy)
    monkeypatch.setitem(app.dependency_overrides, get_game_service, lambda: temp_service)

    created = client.post("/api/v1/games", json={})
    game_id = created.json()["game_id"]
    spy.put_calls.clear()  # only interested in throw/reset write-backs below

    throw_response = client.post(f"/api/v1/games/{game_id}/throws", json=THROW_PAYLOAD)
    assert throw_response.status_code == 200
    assert spy.put_calls == [game_id]

    reset_response = client.post(f"/api/v1/games/{game_id}/reset")
    assert reset_response.status_code == 200
    assert spy.put_calls == [game_id, game_id]
