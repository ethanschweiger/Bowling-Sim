"""Bounded, deterministic replay data for a planar collision run.

`collision.simulate_collision` steps a fixed-timestep solver up to
`MAX_COLLISION_STEPS` (4,000) times. That's far too many states to hand a
client, and the raw solver state isn't a stable contract anyway. This
module defines the small, immutable, versioned shape a *sampled* subset of
those states is published as, so a browser can animate what the server
actually computed instead of inventing motion of its own.

This is an observability layer over the existing 2D model. It records what
the solver already did; it never changes timestep, damping, impulse,
restitution, the fall threshold, termination, or which pins fall.

## Coordinates and time

Positions reuse `collision.py`/`pin_deck.py`'s frame exactly, in inches:

- `x_in` — lateral from lane center, positive toward higher board numbers
  (the bowler's left, matching every other lateral convention here).
- `y_in` — downlane from the headpin plane, which is y=0. The pin rows
  extend toward positive y, so a ball arriving at the deck starts at y=0
  and moves into positive y.

`t_s` is simulation time in seconds from the moment of impact — solver
steps times `COLLISION_DT_S`, not wall-clock and not browser paint time.
It increases strictly across a replay's frames.

## Body identity

`body_id` 0 is always the ball. Every other id is a standing pin's own
1-10 id, already validated by `rack.validate_pin_ids` before the run
began, so a replay can only ever describe pins that genuinely existed for
that impact. Ids are stable across frames and sorted within each frame.

## How a run ends

The last frame is the run's *terminal* snapshot: the final state the
solver computed, whatever that state happens to be. It is deliberately
not called "settled", because the loop has two quite different exits and
only one of them means the motion actually stopped. Every replay carries
`termination_reason` saying which occurred, recorded at the exit itself
rather than guessed afterward from frame count or position:

- `settled` — every simulated body's speed fell below
  `collision.SETTLE_SPEED_IN_S`. That is a planar velocity threshold, and
  the only sense in which this model can say motion ceased. It is not a
  claim that real pins came to rest: these are sliding circles with no
  height, tilt, or toppling, so nothing here observes a pin standing or
  lying down.
- `step_cap` — the solver exhausted `collision.MAX_COLLISION_STEPS`
  (2 seconds of simulated time) while bodies were still moving above that
  threshold. This is a numerical safety stop that bounds the run; it says
  nothing physical at all. Playback of such a run simply ends mid-motion,
  which is honest — the server has no later state to show.

A client must not read either value as "the pins have finished falling",
and must not derive scoring from it. Which pins fell is decided solely by
`collision.FALL_DISPLACEMENT_THRESHOLD_IN` and published as
`fallen_pin_ids`, independently of how the loop terminated.

### The reason is not a function of `steps_taken`

Only these two implications hold, and only in this direction:

- `steps_taken < MAX_COLLISION_STEPS` implies `settled` — stopping early is
  reachable only by the threshold branch.
- `step_cap` implies `steps_taken == MAX_COLLISION_STEPS` — exhausting the
  loop is what the value means.

**At the cap, either reason is possible.** The settle predicate is
evaluated after every step including the last permitted one, so a run whose
final step is exactly where every body crosses below the threshold records
`settled` with `steps_taken == MAX_COLLISION_STEPS`. That is a real,
reachable case, not a technicality: `tests/test_collision_replay.py`
derives one from the damping factor and pins it.

So the converses are false. `steps_taken == MAX_COLLISION_STEPS` does not
imply `step_cap`, and `settled` does not imply an early stop. Reading the
count instead of the field re-introduces exactly the guess this field was
added to eliminate — which is why the value is recorded at the exit and
never recomputed from the data.

## Why 2D only

Flat circles sliding on a plane: no height, tilt, rotation, or toppling
angle. A "fallen" pin here is one that slid past a displacement threshold
(see `collision.FALL_DISPLACEMENT_THRESHOLD_IN`), not one modelled tipping
over. `REPLAY_MODEL_VERSION` is stamped into every replay so a future
solver — 3D or otherwise — cannot silently reinterpret data recorded by
this one.
"""

# Keeps `X | None` usable on this project's Python 3.9 floor — see
# app/physics/throw.py's module docstring for the full explanation.
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

# Bumped whenever the meaning of recorded fields changes, so stored or
# in-flight replay data can never be silently reinterpreted by a different
# solver. Distinct from `PinfallModel.model_id`: that names which model
# resolved the pins, this names the shape/semantics of the replay itself.
#
# v1 -> v2 adds `termination_reason` as a *required* field. A v1 payload
# cannot be upgraded by assuming a value: v1 recorded no distinction, so
# every v1 replay is genuinely ambiguous between the two exits. Consumers
# must reject an unrecognized version and fall back rather than guess —
# which is exactly why the version string exists.
#
# v2 -> v3 changes the sampling cadence from every 100 steps (50 ms, 20 Hz)
# to every 20 steps (10 ms, 100 Hz). That is a version bump rather than a
# server-side tuning detail because the *complete frame schedule* is part of
# the validated wire contract: a consumer derives exactly which frames must
# be present from `dt_s` and `sample_every_steps`, so changing the cadence
# changes what a valid payload is. A v2 payload is still perfectly
# well-formed data — it is simply a different contract, and is refused
# rather than reinterpreted.
#
# Why denser: at 50 ms a 25 mph ball covers roughly 22 in between adjacent
# samples, so an impulse the solver resolved could not become visible until
# up to one whole interval later. 10 ms cuts that to about 4.4 in. This
# records the same run more finely; it does not change the run.
REPLAY_MODEL_VERSION = "planar-collision-replay-2d-v3"

# Why a `Literal` alias rather than an `Enum`: these two values are a wire
# contract shared with the API layer and the browser, and a Literal keeps
# the domain, the Pydantic response model, and the TypeScript union spelled
# as the same two strings, statically checked in each. It also avoids the
# `str, Enum` formatting divergence between Python 3.9 and 3.11 that
# `ball.Coverstock` documents. `schemas.oil_pattern` already establishes
# Literal as this codebase's spelling for a small validated value set.
TerminationReason = Literal["settled", "step_cap"]

#: Every body's speed fell below `collision.SETTLE_SPEED_IN_S` — a planar
#: velocity threshold, not observed physical rest. See "How a run ends".
TERMINATION_SETTLED: TerminationReason = "settled"
#: The solver exhausted `collision.MAX_COLLISION_STEPS` with bodies still
#: moving — a numerical safety stop, carrying no physical meaning.
TERMINATION_STEP_CAP: TerminationReason = "step_cap"

#: Both permitted values, for validators and tests that need the exact set
#: rather than restating it (which is how the two drift apart).
TERMINATION_REASONS: tuple[TerminationReason, ...] = (
    TERMINATION_SETTLED,
    TERMINATION_STEP_CAP,
)

# Sample every Nth solver step, plus a guaranteed initial and final frame.
# At COLLISION_DT_S = 0.0005 s, every 20 steps is one frame per 10 ms — 100
# frames of simulated time per second. Dense enough that a contact becomes
# visible within about 4.4 in of ball travel at the fastest legal release,
# while still turning a full 4,000-step run into 201 frames rather than
# 4,000. Deliberately a fixed count of solver steps, not a wall-clock or
# paint-rate interval: the cadence has to be reproducible on any machine.
REPLAY_SAMPLE_EVERY_STEPS = 20

# Hard ceiling on frames in one replay, enforced (not merely expected) by
# the recorder — the response can never become a solver-step flood.
#
# The arithmetic it has to contain: a full-length run is
# MAX_COLLISION_STEPS / REPLAY_SAMPLE_EVERY_STEPS + 1 = 4000 / 20 + 1 = 201
# frames (steps 0, 20, ... 4000, with 4000 itself on cadence so no extra
# terminal frame is appended). 256 clears that with 55 frames of headroom
# and stays a small, fixed, documented bound rather than an open-ended one.
MAX_REPLAY_FRAMES = 256

BALL_BODY_ID = 0


class _RecordableBody(Protocol):
    """The only three attributes the recorder reads off a solver body.

    Structural rather than importing `collision._Body` directly: `collision`
    imports this module, so depending on its concrete type here would be a
    cycle. Stating the contract this narrowly also makes the recorder's
    read-only nature explicit — it cannot reach velocity, mass, or any
    other solver state even by accident.
    """

    pin_id: int
    x_in: float
    y_in: float


@dataclass(frozen=True)
class ReplayBody:
    """One body's position at one instant. Immutable, presentation-neutral:
    no velocity, contact forces, or other solver internals — a renderer
    needs where things are, and exposing more would make internals a
    contract."""

    body_id: int  # 0 = ball; otherwise the pin's own 1-10 id
    x_in: float
    y_in: float


@dataclass(frozen=True)
class ReplayFrame:
    """All participating bodies at one simulation timestamp."""

    t_s: float
    bodies: tuple[ReplayBody, ...]  # sorted by body_id


@dataclass(frozen=True)
class CollisionReplay:
    """A complete bounded replay of one planar collision run.

    `frames` always holds at least the initial state; a run that actually
    stepped also ends with a frame at its true final step, so the first and
    last frames bracket the whole run regardless of where the sampling
    cadence happened to land. That last frame is the run's terminal
    snapshot — see `termination_reason` for what ended it, and "How a run
    ends" above for why it is not called settled.
    """

    model_version: str
    dt_s: float
    sample_every_steps: int
    steps_taken: int
    frames: tuple[ReplayFrame, ...]  # strictly increasing t_s
    # Which of the solver loop's two exits produced the terminal frame.
    # Recorded at the exit itself, never inferred from the data.
    termination_reason: TerminationReason


class _ReplayRecorder:
    """Collects frames during a run. Internal to the planar model — a
    detail of how `collision.py` produces replay data, never part of
    `simulate_collision`'s public tuple contract.

    Deliberately passive: the recorder reads body positions and appends
    frames. It holds no solver state and can't influence stepping, so
    recording a run and not recording it produce identical physics.
    """

    def __init__(self, dt_s: float, sample_every_steps: int, max_frames: int) -> None:
        self._dt_s = dt_s
        self._sample_every_steps = sample_every_steps
        self._max_frames = max_frames
        self._frames: list[ReplayFrame] = []

    def capture(self, step_index: int, bodies: Sequence[_RecordableBody]) -> None:
        """Record the state after `step_index` completed steps (0 = the
        initial state, before any stepping). Silently ignores a capture
        that would exceed `max_frames` — the bound is enforced here rather
        than trusted to arithmetic elsewhere."""
        if len(self._frames) >= self._max_frames:
            return
        self._frames.append(
            ReplayFrame(
                t_s=step_index * self._dt_s,
                bodies=tuple(
                    ReplayBody(body_id=body.pin_id, x_in=body.x_in, y_in=body.y_in)
                    for body in sorted(bodies, key=lambda b: b.pin_id)
                ),
            )
        )

    def should_capture(self, step_index: int) -> bool:
        return step_index % self._sample_every_steps == 0

    def finish(
        self,
        steps_taken: int,
        bodies: Sequence[_RecordableBody],
        termination_reason: TerminationReason,
    ) -> CollisionReplay:
        """Close the replay, guaranteeing a final frame at the true end of
        the run. If the cadence already captured exactly that step, this
        doesn't duplicate it — timestamps stay strictly increasing.

        `termination_reason` is supplied by the caller because only the
        solver loop knows which of its exits it actually took. The recorder
        stays passive: it stamps the value it is handed and never examines
        positions, step counts, or frames to decide one for itself.
        """
        final_t_s = steps_taken * self._dt_s
        if steps_taken > 0 and (not self._frames or self._frames[-1].t_s < final_t_s):
            self.capture(steps_taken, bodies)
        return CollisionReplay(
            model_version=REPLAY_MODEL_VERSION,
            dt_s=self._dt_s,
            sample_every_steps=self._sample_every_steps,
            steps_taken=steps_taken,
            frames=tuple(self._frames),
            termination_reason=termination_reason,
        )
