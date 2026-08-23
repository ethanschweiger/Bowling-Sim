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
from typing import Protocol

# Bumped whenever the meaning of recorded fields changes, so stored or
# in-flight replay data can never be silently reinterpreted by a different
# solver. Distinct from `PinfallModel.model_id`: that names which model
# resolved the pins, this names the shape/semantics of the replay itself.
REPLAY_MODEL_VERSION = "planar-collision-replay-2d-v1"

# Sample every Nth solver step, plus a guaranteed initial and final frame.
# At COLLISION_DT_S = 0.0005 s, every 100 steps is one frame per 50 ms —
# 20 frames of simulated time per second, enough for smooth playback,
# while turning a full 4,000-step run into roughly 42 frames rather than
# 4,000. Deliberately a fixed count of solver steps, not a wall-clock or
# paint-rate interval: the cadence has to be reproducible on any machine.
REPLAY_SAMPLE_EVERY_STEPS = 100

# Hard ceiling on frames in one replay, enforced (not merely expected) by
# the recorder. With the cadence above a full-length run lands near 42, so
# this leaves generous headroom while still guaranteeing the response can
# never become a solver-step flood.
MAX_REPLAY_FRAMES = 64

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
    stepped also ends with a final settled frame, so the first and last
    frames bracket the whole run regardless of where the sampling cadence
    happened to land.
    """

    model_version: str
    dt_s: float
    sample_every_steps: int
    steps_taken: int
    frames: tuple[ReplayFrame, ...]  # strictly increasing t_s


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

    def finish(self, steps_taken: int, bodies: Sequence[_RecordableBody]) -> CollisionReplay:
        """Close the replay, guaranteeing a final frame at the true end of
        the run. If the cadence already captured exactly that step, this
        doesn't duplicate it — timestamps stay strictly increasing."""
        final_t_s = steps_taken * self._dt_s
        if steps_taken > 0 and (not self._frames or self._frames[-1].t_s < final_t_s):
            self.capture(steps_taken, bodies)
        return CollisionReplay(
            model_version=REPLAY_MODEL_VERSION,
            dt_s=self._dt_s,
            sample_every_steps=self._sample_every_steps,
            steps_taken=steps_taken,
            frames=tuple(self._frames),
        )
