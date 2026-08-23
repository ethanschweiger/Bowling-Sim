# Planar collision calibration note

A reference corpus for the 2D collision model, and an honest statement of
what it is worth.

This is a **measurement baseline, not a calibration**. Every number here was
read off the current solver. None of it was measured on a real lane, and
none of it is evidence that the model reproduces real pin carry. Its only
job is to make the next parameter or termination-policy change arguable from
a diff instead of from an impression of the animation.

Corpus and assertions: [`backend/tests/test_calibration_corpus.py`](../tests/test_calibration_corpus.py).

## How to rerun it

```bash
cd backend && .venv/bin/python -m pytest tests/test_calibration_corpus.py -q
```

The run is fully deterministic — no clock, no RNG, no sampling — so a
difference is always a model change, never noise. If a value below changes,
update this note and the expectation table in the same commit and say which
assumption moved. A silent update throws away the baseline.

## Conventions

Units are inches, seconds, degrees, and miles per hour at the boundary.
Positions in a replay are always inches.

| Quantity | Convention |
|---|---|
| `lateral_position_in` / `x_in` | Inches from lane center. Positive toward **higher board numbers** (board 1 is the right gutter, 39 the left), i.e. the bowler's left. |
| `y_in` | Inches downlane from the **headpin plane**, which is `y = 0`. Pin rows extend toward positive `y`. |
| `heading_deg` | Degrees off straight-ahead, same sign convention as entry angle. |
| `t_s` | Seconds of **simulation** time since impact — solver steps times the timestep. Not wall-clock, not paint time. |
| `body_id` | `0` is the ball; every other id is that pin's own 1–10 id. |

Pin geometry (`app/physics/pin_deck.py`): pin 1 at lateral 0, pin 2 at +6 in,
pin 3 at −6 in, back row 7/8/9/10 at +18/+6/−6/−18 in. So a **negative**
line arrives on the 1-3 side — the pocket for a right-handed bowler — and a
positive line crosses to the 1-2 side.

## What is being characterized

Model: `planar-collision-2d-v1`, replay schema `planar-collision-replay-2d-v2`.

| Constant | Value | Source |
|---|---|---|
| `COLLISION_DT_S` | 0.0005 s | chosen |
| `MAX_COLLISION_STEPS` | 4000 (= 2.0 s) | chosen safety cap |
| `SETTLE_SPEED_IN_S` | 0.5 in/s | chosen |
| `LINEAR_DAMPING_PER_S` | 1.2 /s (factor 0.9994 per step) | chosen |
| `PIN_EFFECTIVE_RADIUS_IN` | 2.383 in | half of USBC max diameter 4.766 in |
| `COLLISION_RESTITUTION` | 0.67 | USBC pin-to-pin coefficient |
| `FALL_DISPLACEMENT_THRESHOLD_IN` | 2.383 in | chosen — equal to the pin radius |
| `PIN_MASS_BLOB` | 0.009065290814529331 | from USBC target weight 3 lb 8 oz |
| `REPLAY_SAMPLE_EVERY_STEPS` | 100 (one frame per 50 ms) | chosen |
| `MAX_REPLAY_FRAMES` | 64 | chosen bound |

Two of these come from USBC equipment specifications (pin diameter,
restitution, weight). **Everything else is an effective 2D assumption**, not
a calibrated value: the damping rate, the settle threshold, the step cap, and
above all the fall criterion. A pin here "falls" by sliding past a
displacement threshold — it is a flat circle with no height, no tilt, no
rotation, and no toppling angle, so nothing in this model observes a pin
standing up or lying down. Equating that threshold to the pin radius is a
stated choice, not a measurement.

Ball inputs are held fixed across the corpus: 15.0 lb, radius 4.29 in, lane
condition version 1.

## The corpus

| Case | lateral in | heading ° | mph | Standing rack | Fallen | Steps | Reason | Final `t_s` | Frames |
|---|---|---|---|---|---|---|---|---|---|
| `pocket` | −2.6 | +1.4 | 17 | full 1–10 | 1, 3, 5, 6, 8, 9, 10 | 4000 | `step_cap` | 2.0000 | 41 |
| `light_hit` | 0.0 | 0.0 | 17 | full 1–10 | 1, 2, 3, 5, 8, 9, 10 | 4000 | `step_cap` | 2.0000 | 41 |
| `brooklyn` | +3.0 | −2.0 | 17 | full 1–10 | 1, 2, 4, 5, 7, 8, 9 | 4000 | `step_cap` | 2.0000 | 41 |
| `spare_3_6_10` | −8.0 | −2.0 | 16 | 3, 6, 10 | 3, 6, 10 | 4000 | `step_cap` | 2.0000 | 41 |
| `low_energy_settle` | −8.0 | 0.0 | 0.05 | full 1–10 | none | 942 | `settled` | 0.4710 | 11 |
| `terminal_settle` | −30.0 | 0.0 | 0.3132900502327218 | full 1–10 | none | 4000 | `settled` | 2.0000 | 41 |

`terminal_settle`'s speed is **derived, not chosen**: it is the midpoint of
the interval of release speeds whose damping curve crosses
`SETTLE_SPEED_IN_S` exactly on the last permitted step. It follows the
constants automatically, and a test checks it still straddles that step, so
the case cannot quietly decay into an ordinary early settle or an ordinary
cap.

### Termination reasons

A reason names the solver exit that actually fired. It is **not** a claim
that a real pin deck finished doing anything.

- `settled` — every body dropped below `SETTLE_SPEED_IN_S`. A velocity
  threshold on sliding circles.
- `step_cap` — the loop used all 4000 permitted steps with bodies still
  moving. A numerical safety stop with no physical meaning.

All three reachable categories are in the corpus: an ordinary contact run
that exhausts the loop (`pocket`), an early settle (`low_energy_settle`),
and a threshold crossing on the final permitted step (`terminal_settle`).
The fourth combination — `step_cap` before the cap — is impossible, and a
test asserts it never appears. Note that `steps_taken == 4000` does **not**
imply `step_cap`; see "The reason is not a function of `steps_taken`" in
`app/physics/replay.py`.

## What this corpus validates

- The solver is deterministic: identical input reproduces a byte-identical
  replay, fallen set, step count, and reason.
- Recording is passive: the public non-recorded `simulate_collision` result
  matches the recorded run for every case.
- Structural invariants hold: fallen ids are sorted, unique, and a subset of
  the rack that was actually standing; every frame carries exactly the ball
  plus that rack; timestamps strictly increase from 0; positions stay finite
  and bounded; every run stays within the declared step, duration, and frame
  caps.
- All three termination categories occur and are labelled by the exit that
  fired.

## What this corpus does *not* validate

- **Any resemblance to real pin carry.** The fallen sets above are what this
  2D model produces. Whether a real 17 mph pocket hit leaves those pins is
  an open question this corpus says nothing about.
- **Toppling, kickbacks, deflection off the pit, or pin-on-pin scatter in
  three dimensions.** None of it is modelled. There is no lane, kickback, or
  pit collider at all.
- **A settled physical deck.** Every ordinary contact case above ends at the
  step cap with bodies still moving. That is an honest bounded-solver result
  and nothing more; the model has never been shown to reach rest.

### Known blind spot: the model has no marginal outcomes

Worth stating plainly, because it limits what this baseline can detect. In
every full-rack case, each pin either is never touched at all — final
displacement **exactly 0.00 in** — or is driven 112 to 264 in from its spot.
Nothing lands anywhere near the 2.383 in fall threshold.

Measured consequences:

- Raising `FALL_DISPLACEMENT_THRESHOLD_IN` by up to **+25%** changes no
  corpus outcome; +26% is the first change.
- *Lowering* it changes nothing at any magnitude, because an untouched pin
  sits at exactly zero displacement.
- `COLLISION_RESTITUTION` is the sensitive one: **+8%** already changes an
  outcome.

So this corpus is a good tripwire for changes to the impulse response and a
poor one for the fall criterion. Real bowling is full of marginal taps and
slow wobbles; this model produces none, which is itself a limitation of flat
circles and a strong hint about where a future 3D solver would differ most.

## Relationship to scoring

None of this feeds scoring decisions. Which pins fell is
`fallen_pin_ids`, decided solely by the displacement threshold; the replay
and its termination reason are published observation over a run that already
happened. A client must not derive pin state, carry, or score from a
termination reason.
