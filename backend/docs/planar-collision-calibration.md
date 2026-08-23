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

**Three** inputs are traceable to USBC equipment specifications: maximum pin
diameter, target pin weight, and the published pin coefficient of
restitution. Even those are not used as a calibration of this model:

- The diameter sets a circle radius for a body that has no height, so it is a
  footprint, not a pin.
- `COLLISION_RESTITUTION` reuses the **pin-to-pin** coefficient as the
  restitution for *every* contact in this model, ball-to-pin included. That
  is a deliberate simplification — one number standing in for contacts the
  model does not distinguish — not a measured ball-pin coefficient and not a
  literal pin-to-pin calibration of a real collision.

**Everything else is an effective 2D assumption**: the damping rate, the
settle threshold, the step cap, and above all the fall criterion. A pin here
"falls" by sliding past a displacement threshold — it is a flat circle with
no height, tilt, rotation, or toppling angle, so nothing in this model
observes a pin standing up or lying down. Equating that threshold to the pin
radius is a stated choice, not a measurement.

Ball inputs are held fixed across the corpus: 15.0 lb, radius 4.29 in, lane
condition version 1.

## The corpus

| Case | lateral in | heading ° | mph | Standing rack | Fallen | Steps | Reason | Final `t_s` | Frames |
|---|---|---|---|---|---|---|---|---|---|
| `pocket` | −2.6 | +1.4 | 17 | full 1–10 | 1, 3, 5, 6, 8, 9, 10 | 4000 | `step_cap` | 2.0000 | 41 |
| `head_on` | 0.0 | 0.0 | 17 | full 1–10 | 1, 2, 3, 5, 8, 9, 10 | 4000 | `step_cap` | 2.0000 | 41 |
| `light_hit` | −6.0 | +1.4 | 17 | full 1–10 | 3, 5, 6, 7, 9 | 4000 | `step_cap` | 2.0000 | 41 |
| `brooklyn` | +3.0 | −2.0 | 17 | full 1–10 | 1, 2, 4, 5, 7, 8, 9 | 4000 | `step_cap` | 2.0000 | 41 |
| `spare_3_6_10` | −8.0 | −2.0 | 16 | 3, 6, 10 | 3, 6, 10 | 4000 | `step_cap` | 2.0000 | 41 |
| `low_energy_settle` | −8.0 | 0.0 | 0.05 | full 1–10 | none | 942 | `settled` | 0.4710 | 11 |
| `terminal_settle` | −30.0 | 0.0 | 0.3132900502327218 | full 1–10 | none | 4000 | `settled` | 2.0000 | 41 |

### What makes `light_hit` light

Geometrically, not by name. Two circles touch when their centres are closer
than the sum of their radii — here ball 4.29 in + pin 2.383 in = **6.673 in**.
The ball starts on the headpin plane and pin 1 stands at lateral 0, so for
the headpin the initial centre separation is simply `|lateral|`, and the
overlap is `6.673 − |lateral|`.

| Line | lateral | overlap | as a fraction of contact |
|---|---|---|---|
| `head_on` | 0.0 | 6.673 in | 100% |
| `pocket` | −2.6 | 4.073 in | 61% |
| `light_hit` | −6.0 | 0.673 in | **10%** |
| (no contact at all) | ≥ ±6.673 | — | 0% |

At 10% overlap the ball genuinely contacts the headpin but only grazes it:
pin 1 is displaced **0.547 in**, far short of the 2.383 in fall threshold, so
it is left standing while the ball carries on into the 3-5-6-7-9. Leaving
the headpin up is what a light hit means at the deck, and here it falls out
of the geometry rather than being asserted.

The corpus tests use a quarter of the contact distance as the "light" bound —
chosen for margin, since the three lines above sit at 10%, 61%, and 100% —
and check that the bound actually rejects the solid lines rather than merely
admitting the thin one.

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

## Sensitivity: what this corpus can detect

The corpus brackets the fall threshold closely from both sides, so a change
to it is caught quickly — but by two specific pins, not by the corpus at
large. Final displacements:

| Case | pin 1 | other moved pins | untouched (exactly 0.00 in) |
|---|---|---|---|
| `pocket` | **3.302 in** | 46.6 – 171.3 | 2, 4, 7 |
| `head_on` | **5.289 in** | 25.5 – 251.8 | 4, 6, 7 |
| `brooklyn` | **2.979 in** | 98.5 – 145.2 | 3, 6, 10 |
| `light_hit` | **0.547 in** (stays up) | 38.4 – 238.6 | 2, 4, 8, 10 |
| `spare_3_6_10` | — | 112.3 – 264.4 | none |

So displacements are not bimodal. Secondary pins are indeed flung across the
deck, but the pin the ball strikes first ends up within a few inches of its
spot. Those first-struck headpins are what sit near the threshold — and they
sit at quite different distances from it:

| Case | pin 1 | relative to the 2.383 in threshold |
|---|---|---|
| `light_hit` | 0.547127 in | **below** — moved, but stands |
| `brooklyn` | 2.979317 in | falls, clearing it by only **25%** |
| `pocket` | 3.301906 in | falls, clearing it by 39% |
| `head_on` | 5.289243 in | falls, clearing it by 122% — well above |

Only `brooklyn` is near the current threshold from above, and only
`light_hit` from below. `pocket` and `head_on` are not close to it in either
direction.

### The intervals

The fall predicate is `displacement >= threshold`, so a boundary belongs to
the *falling* side. Those two headpins are the only movements anywhere near
the threshold, which makes the bands exact. Each is backed by a focused test
that varies the constant through `monkeypatch` (restored at teardown) and
checks **every** corpus case, not just one.

| Threshold | Effect |
|---|---|
| > 2.979317277 | `brooklyn` loses pin 1: `(1,2,4,5,7,8,9)` → `(2,4,5,7,8,9)` |
| **(0.547127276, 2.979317277]** | **no corpus outcome changes** — contains the default 2.383 |
| ≤ 0.547127276, and > 0 | `light_hit` gains pin 1: `(3,5,6,7,9)` → `(1,3,5,6,7,9)` |
| exactly 0 | every standing pin in every case is reported fallen |

Notes on the edges:

- The upper crossing is at 2.979317277 / 2.383 = **×1.250238** (+25.02%).
  ×1.2502 changes nothing; ×1.2503 changes `brooklyn` alone.
- The lower boundary is **inclusive on the changing side**: at exactly
  0.547127276 the light hit's pin 1 satisfies `>=` and falls. So the
  unchanged interval is open at the bottom, and rounding it to "(0, 2.383]"
  would state something false.
- Zero is a *different predicate* again, not the limit of the row above:
  displacement is non-negative, so at zero even an untouched pin satisfies
  `>=` and the whole rack is reported down. Both contact-free settle cases
  go from no pins to ten.

Each crossing reclassifies exactly one pin in exactly one case. Nothing else
in the corpus lies in these bands.

### Corrections to earlier versions of this note

Recorded because a baseline is only useful if its history is honest.

- The first version claimed moved pins all travel 112–264 in and that no
  outcome is marginal, concluding this corpus is a poor fall-threshold
  tripwire. Wrong in both directions: the range came from the
  `spare_3_6_10` row alone and was generalised, and the conclusion is the
  opposite of what the numbers show.
- The second version fixed that but claimed no outcome changes anywhere on
  (0, 2.383]. Adding the light hit in that same commit is what made the
  claim false — its pin 1 sits inside that interval — and the regression
  behind it checked only `pocket`, which is above every boundary and so
  could not have caught it. Both are corrected above, and the lowering test
  is now corpus-wide with the boundary read from the replay itself.

### Not characterised here

`COLLISION_RESTITUTION` sensitivity was noted in the earlier version without
a test behind it, so it has been removed rather than left as an unverified
number. Characterising it is a separate measurement, not a claim this corpus
currently supports.

## Relationship to scoring

None of this feeds scoring decisions. Which pins fell is
`fallen_pin_ids`, decided solely by the displacement threshold; the replay
and its termination reason are published observation over a run that already
happened. A client must not derive pin state, carry, or score from a
termination reason.
