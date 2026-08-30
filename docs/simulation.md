# Simulation and collision model

The simulator aims for deterministic, explainable, directionally credible ball
motion. It is not a rigid-body solver or a fitted predictor of real bowling
outcomes.

## Skid, hook, and roll

One integration loop advances the ball in `0.05 ft` downlane increments. A
release starts with forward velocity, launch heading, and a bounded lateral-slip
reservoir. Axis rotation creates the main sideways contact component; RG
differential supplies a smaller track-flare-inspired residual. Coverstock and
surface determine how strongly the ball can use available traction.

- In the oiled heads, low friction converts little slip, producing skid.
- As the oil thins, traction rises and converts slip into lateral velocity,
  producing a continuous hook.
- Once the reservoir is spent, lateral acceleration approaches zero and the
  ball rolls on its new heading.

No code branch switches between named phases at a fixed distance. A ball can
remain in skid or roll early when its inputs and lane state lead there.

## Coordinates and time

Downlane distance starts at the foul line and reaches `60 ft` at the headpin
plane. Board 1 is the bowler's right gutter, board 20 is center, and board 39 is
left. Positive lateral movement and entry angle point toward higher board
numbers. A conventional right-handed line therefore launches from around board
28 with a negative angle before hooking back toward higher boards.

Every trajectory point records elapsed simulation time. The browser interpolates
the returned points using those timestamps and a bounded display scale; it does
not run a second clock or physics model.

## Stateful lane condition

An oil pattern is an immutable definition; a lane condition is a versioned oil
grid for one game. A completed throw removes a small amount of oil along its path
and carries a fraction downlane. Applying wear returns a new condition, allowing
the simulation itself to remain pure. Reset restores the game's original named
pattern.

The bundled `house` and `challenge` patterns are modeling assumptions. Their
length, taper, ratio, and total volume are not certified pattern data.

## Seeded release variance

The API samples bounded error around speed, rev rate, axis rotation, axis tilt,
launch angle, and laydown board. Supplying a seed reproduces the sampled release;
omitting one causes the server to generate and return a seed. Bounds are product
choices for this model, not measurements of human variance.

## Impact and pinfall

The trajectory's canonical terminal position, heading, and speed become the ball
impact state. The collision solver operates on the subset of pins still standing
and resolves deterministic 2D circle collisions with bounded damping and fall
thresholds. Its output is a set of fallen pin IDs. The immutable rack validates
and removes those pins; the scorecard receives only the count.

Pin dimensions, pin weight, and coefficient of restitution are sourced from the
USBC equipment manual. Effective collision radius, damping, and fall threshold
are stated calibration choices. The measured regression corpus and sensitivity
boundaries are in
[`backend/docs/planar-collision-calibration.md`](../backend/docs/planar-collision-calibration.md).

## Interpreting results

Use the simulator to compare how inputs influence one internally consistent
model. Entry boards, breakpoints, hook magnitude, and exact pinfall should not be
treated as predictions for a physical lane. See [limitations.md](limitations.md).
