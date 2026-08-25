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

The run is fully deterministic — no clock and no RNG, and its replay
sampling is a *fixed* count of solver steps rather than anything
wall-clock or machine dependent — so a
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

Model: `planar-collision-2d-v1`, replay schema `planar-collision-replay-2d-v3`.

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
| `REPLAY_SAMPLE_EVERY_STEPS` | 20 (one frame per 10 ms, 100 Hz) | chosen |
| `MAX_REPLAY_FRAMES` | 256 | chosen bound |

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
| `pocket` | −2.6 | +1.4 | 17 | full 1–10 | 1, 3, 5, 6, 8, 9, 10 | 4000 | `step_cap` | 2.0000 | 201 |
| `head_on` | 0.0 | 0.0 | 17 | full 1–10 | 1, 2, 3, 5, 8, 9, 10 | 4000 | `step_cap` | 2.0000 | 201 |
| `light_hit` | −6.0 | +1.4 | 17 | full 1–10 | 3, 5, 6, 7, 9 | 4000 | `step_cap` | 2.0000 | 201 |
| `brooklyn` | +3.0 | −2.0 | 17 | full 1–10 | 1, 2, 4, 5, 7, 8, 9 | 4000 | `step_cap` | 2.0000 | 201 |
| `spare_3_6_10` | −8.0 | −2.0 | 16 | 3, 6, 10 | 3, 6, 10 | 4000 | `step_cap` | 2.0000 | 201 |
| `low_energy_settle` | −8.0 | 0.0 | 0.05 | full 1–10 | none | 942 | `settled` | 0.4710 | 49 |
| `terminal_settle` | −30.0 | 0.0 | 0.3132900502327218 | full 1–10 | none | 4000 | `settled` | 2.0000 | 201 |

### Where the frame counts come from

The recorder emits step 0, every cadence tick, and one terminal frame when
the final step is not itself a tick:

- A full 4,000-step run: 4000 / 20 + 1 = **201** frames. Step 4000 *is* a
  tick, so no extra terminal frame is appended.
- `low_energy_settle` stops at step 942, which is not a tick: ticks 0…940 is
  48 frames, plus one terminal frame at 942 = **49**.

Both sit inside the 256-frame cap. These counts are the v3 cadence's, and
were 41 and 11 under v2's 100-step / 50 ms sampling — the only corpus values
that moved with that change; see "What v3 changed" below.

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

| Case | pin 1 (approx.) | relative to the 2.383 in threshold |
|---|---|---|
| `light_hit` | ≈ 0.547127 in | **below** — moved, but stands |
| `brooklyn` | ≈ 2.979317 in | falls, clearing it by only **25%** |
| `pocket` | ≈ 3.301906 in | falls, clearing it by 39% |
| `head_on` | ≈ 5.289243 in | falls, clearing it by 122% — well above |

Every decimal in this document is an approximation for reading, never a
threshold to compare against; the tests derive the values they probe.

Only `brooklyn` is near the current threshold from above, and only
`light_hit` from below. `pocket` and `head_on` are not close to it in either
direction.

### Two local crossings

Call the two bracketing displacements `d_light` and `d_brooklyn`. **They are
derived from the recorded replay, not written down**, and the tests read them
the same way. Approximately:

    d_light     ≈ 0.5471272764  in   (light_hit pin 1)
    d_brooklyn  ≈ 2.9793172773  in   (brooklyn  pin 1)

Those decimals are for orientation only and **must not be used as interval
endpoints**. A rounded literal is a different float from the value it
approximates, so an inequality written against it describes a threshold the
model does not have. Concretely: 0.5471272762 is greater than a 9-decimal
rendering of `d_light` yet still below `d_light` itself, so the light hit's
headpin still falls there.

The fall predicate is `displacement >= threshold`, so each boundary belongs
to the *falling* side. What is actually proven is the behaviour **at** each
boundary and at the single adjacent representable float
(`math.nextafter(boundary, +inf)`), compared across **every** corpus case:

| Threshold | Result |
|---|---|
| `nextafter(d_brooklyn, +inf)` | `brooklyn` loses pin 1: `(1,2,4,5,7,8,9)` → `(2,4,5,7,8,9)`; all other cases unchanged |
| `d_brooklyn` | baseline preserved everywhere |
| *(the default 2.383 lies between)* | baseline |
| `nextafter(d_light, +inf)` | baseline preserved everywhere |
| `d_light` | `light_hit` gains pin 1: `(3,5,6,7,9)` → `(1,3,5,6,7,9)`; all other cases unchanged |
| exactly 0 | every standing pin in every case is reported fallen |

### Scope of these statements — deliberately narrow

Each row above is a **local** observation at one threshold, not a claim about
a range. In particular, "past `d_brooklyn`, Brooklyn loses pin 1" describes
one representable step past that boundary — **not** every higher threshold.
Move further and other crossings appear: at 3.4 in, past pocket's own headpin
at ≈3.30 in, `pocket` changes too. A test asserts exactly that, so the limit
is demonstrated rather than merely disclaimed.

A full sweep of the threshold range is not attempted in this milestone. What
is established is: the production value sits between two adjacent crossings;
each of those crossings reclassifies exactly one pin in exactly one case; and
larger excursions cross more.

Between the sampled interior points (2.0, 1.0, 0.6 above the lower boundary;
0.5, 0.1, 1e-9 below it) the baseline holds, which shows the unchanged region
has real width rather than being a float hairline — but sampling is not proof
of invariance across the whole band, and is not offered as such.

Zero remains a *different predicate*, not the limit of the rows above:
displacement is non-negative, so at zero even an untouched pin satisfies `>=`
and the whole rack is reported down. Both contact-free settle cases go from
no pins to ten.

### What v3 changed

The replay schema moved from `planar-collision-replay-2d-v2` to
`...-v3`, raising the sampling cadence from every 100 solver steps (50 ms,
20 Hz) to every 20 (10 ms, 100 Hz) and the frame cap from 64 to 256.

The reason is visible latency, not physics: at 50 ms a 25 mph ball covers
roughly 22 in between adjacent samples, so an impulse the solver had already
resolved could go unseen for up to a whole interval. 10 ms cuts that to about
4.4 in.

Across this entire corpus, **`Frames` is the only column that moved** —
fallen ids, step counts, termination reasons and final simulation times are
all identical. That is what a recording-density change should look like, and
it is the evidence that the solver itself was untouched: recording remains
passive, and the version bump exists because the complete frame schedule is a
validated wire contract, not because any run changed.

## Pocket carry: investigated, not tuned

A legitimate right-handed pocket entry ends at about seven pins. We asked
whether the existing collision constants can be calibrated to give
believable *typical* carry. The answer is **no**, and the blocker is
structural rather than a coefficient, so no constant was changed.

Corpus and evidence: [`backend/tests/test_pocket_carry_corpus.py`](../tests/test_pocket_carry_corpus.py).

```bash
cd backend && .venv/bin/python -m pytest tests/test_pocket_carry_corpus.py -q
```

Fully deterministic, no clock/RNG/network dependence; a difference is always
a model change.

### The representative case

An end-to-end reactive house shot — seed 17, board 28, −1.5° — enters at
board 16.686, 1.32°, 16.25 mph (the rounded presentation fields), and knocks
**7**, leaving the **2-4-7**.

The `ImpactState` that shot actually produces — derived through the same
`sample_release → simulate_throw → impact_state_from_result` pipeline both
HTTP throw routes use, from the unrounded trajectory endpoint — is
**lateral −3.480 in, heading +1.321°, speed 16.249 mph**. That is *not* the
same as a tidy hand-placed `(−2.6 in, +1.4°, 17 mph)` control: it differs by
about 0.88 in laterally, 0.08° in heading, and 0.75 mph in speed. The two
happen to knock the same seven pins today, which is not equivalence of
input state — an earlier version of this note called the tidy control an
exact reproduction of the seeded shot, which was wrong.

What is actually verified: calling the collision solver directly on the
seeded shot's derived `ImpactState` reproduces the **complete** recorded
run of both the game-scoped and the legacy HTTP throw routes exactly —
fallen ids, step count, termination reason, and the full ordered
threshold-crossing set, not just the fallen count. See
`test_the_seeded_impact_reproduces_exactly_through_the_game_route` and its
legacy-route counterpart.

The tidy `(−2.6, +1.4, 17)` line is kept in the corpus as its own named case
(`pocket`) — a synthetic, hand-placed control on the credible right-handed
entry range, used throughout this section for the sweep and grid
measurements below. It is never claimed to be the seeded shot.

### Why the constants cannot fix it

| Lever | Effect on pocket carry | Why |
|---|---|---|
| `FALL_DISPLACEMENT_THRESHOLD_IN` | none | The survivors are never contacted — displacement **exactly 0.00 in**. Nothing sits between zero and the threshold, so lowering it reclassifies nothing. |
| `LINEAR_DAMPING_PER_S` | none across 1.2 → 0.05 | Extra travel time cannot help a pin that receives no impulse. |
| `COLLISION_RESTITUTION` | mean stays under 7, max never exceeds 8, across 0.605–0.95 | Swept over the full USBC-published range (0.605–0.735) and materially beyond it (to 0.95), against the 20-line entry sweep below. At the production default (0.670) the sweep's mean is exactly 6.55 and its max is 8; at every other tested value the mean stays under 7.0 and the max is always exactly 8, never 9. Each named control is also pinned individually at every swept value, at the production coupled radius/threshold, so no value can flatten a control without failing a test. The one place nine appears at all is the seeded representative line — which is *not* one of the 20 sweep lines — at restitution 0.95, about 29% above the USBC-published maximum, and even there the thin hit is still 5 and the corner still 7. See `test_restitution_never_lifts_typical_pocket_carry_to_a_credible_strike`, `test_every_named_control_matches_its_documented_outcome`, `test_restitution_never_breaks_the_control_ordering`, and `test_only_an_uncertified_restitution_reaches_nine_and_only_on_the_seeded_line`. |
| `PIN_EFFECTIVE_RADIUS_IN` (coupled with its fall threshold) | reaches 9–10, but the corner control always rises with it | Tested with `FALL_DISPLACEMENT_THRESHOLD_IN` set to the same value at every step, since the production model defines the threshold as equal to the radius — a decoupled radius-only experiment does not test the rule actually being claimed about. It inflates the corner control *before* it helps the pocket at all (+1 corner at 2.6 in, pocket still 7). At production restitution's first nine-carrying cell (0.670 / 3.6 in) the readout is pocket 9, thin 8, corner 8, flush headpin 10 — but that is one cell, not the rule: across all thirteen nine-carrying cells the thin hit runs as low as 7 and the flush headpin as low as 9. Nor does every control collapse: the thin hit still clears its own margin in 4 of the 13. What holds grid-wide is a single blocker — the corner control is never three or more pins behind the pocket in any of the 13, which is exactly what the discriminating predicate requires. See `test_every_cell_that_reaches_nine_lifts_the_corner_control_with_it`. |

A bounded, explicit 49-cell grid — seven restitution values (the USBC
low/target/high plus four exploratory values to 0.90) crossed with seven
coupled radius/threshold values (2.383–3.6 in) — finds **zero** combinations
reaching nine at the pocket while keeping the light, corner, outside, and
flush-hit controls distinguishable from it. Exactly 13 of the 49 cells reach
nine — that count is asserted by the grid test, not merely quoted here — and
the corner control blocks every one of the 13. See
`test_no_grid_cell_reaches_the_pocket_threshold_while_staying_discriminating`
for the exact axes and predicate,
`test_every_cell_that_reaches_nine_lifts_the_corner_control_with_it` for the
grid-wide statement and the counterexamples that rule out a stronger one, and
`test_3_6_is_the_lowest_declared_radius_reaching_nine_at_production_restitution`
for one concrete cell's full readout.

Holding restitution at the production default (0.670), the pocket's count
across the declared radii runs 7, 7, 7, 7, 7, 8, 9 — so 3.6 in is the lowest
declared radius reaching nine *at that restitution*, proved against every
lower declared radius rather than assumed. It is **not** the lowest in the
grid: raising restitution reaches nine at smaller radii, and 0.90 already
does it at 3.0 in.

### The structural reason

Real 2-4-7 carry comes from the headpin deflecting *left* into the 2, which
drives the 4 and then the 7. Verified on **both** cases: the headpin moves
about 3.3 in on the synthetic `pocket` control and about 2.6 in on the
actual seeded shot, while every pin the ball or the chain genuinely strikes
moves 40 in or more on either. Two causes, neither of them a coefficient:

- The ball begins the run already overlapping the headpin — by about 4.07 in
  on the synthetic control and 3.19 in on the seeded shot, since it starts
  on the headpin plane — so that first contact is resolved largely by
  positional correction rather than by an impulse.
- A flat disc cannot sweep the deck the way a 15 in pin topples across it.
  The swept volume of a falling pin is most of what takes out a neighbouring
  row, and this model has no pose at all.

Believable carry therefore needs a model change — a pin that topples, or an
impact that begins before contact rather than inside it — not a different
number. Forcing nine by widening the pins would buy the headline figure by
making every shot carry, which is a worse model than one that carries too
little.

### Verified data, product assumptions, and unmeasured questions

To keep this section's claims from blurring together:

**Verified by an executable test in the corpus:**
- The seeded shot's exact `ImpactState` fields, and that the direct
  collision call on them reproduces both HTTP routes' complete recorded
  run — ids, steps, reason, and crossings — exactly.
- That the pocket's untouched survivors (2, 4, 7) have exactly zero
  displacement, on both the seeded shot and the synthetic `pocket` control.
- That the headpin barely moves on both, because both the seeded and the
  controlled lateral positions start well inside the headpin's contact
  radius.
- That damping has no effect on any corpus case across a twenty-fold range.
- That restitution, swept across the USBC range and materially beyond it,
  never lifts the 20-line sweep's mean above 7 or its max above 8.
- That every named control — the seeded representative shot plus the thin,
  corner, near-channel, and flush-headpin hits — holds its exact documented
  count at every swept restitution value, at the production coupled
  radius/threshold, and that the pocket's ordering over them never breaks.
- That exactly 13 of the grid's 49 cells reach nine, that 3.6 in is the
  lowest declared radius reaching nine at production restitution, and that
  the corner control sits within two pins of the pocket in all 13.
- That the coupled radius/threshold, swept across a 49-cell grid against
  restitution, never reaches nine at the pocket while keeping the named
  controls distinguishable.

**Product assumptions, not measurements:** the specific discriminating
predicate above (light ≤ pocket−2, corner ≤ pocket−3, outside ≤ 2, and so
on) is a judgement call about what "still tells shots apart" should mean,
not a measured fact — a different, still-defensible predicate could in
principle draw the line elsewhere. The 20-line and 49-cell axes are a
chosen, bounded sample of the credible right-handed entry range, not an
exhaustive one.

**Genuinely unmeasured, and not claimed anywhere above:** `PIN_MASS_BLOB`,
the ball's own mass and radius, lane-condition/oil-pattern sensitivity,
entry speeds outside the roughly 16–17 mph range swept here, and any
three-parameter joint tuning (for example restitution, radius, and damping
together, or the fall threshold deliberately decoupled from radius). None
of these were tuned, swept, or ruled out in this investigation — they are
open questions for a future one, not conclusions this note draws.

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
  could not have caught it.
- The third version stated the corrected intervals with **rounded decimal
  endpoints**, which makes the inequalities false in the gap between the
  printed value and the real one. It also gave "> `d_brooklyn` ⇒ Brooklyn
  loses pin 1" as if it held for every larger threshold, when a bigger move
  crosses pocket's headpin as well; and it described both boundary checks as
  corpus-wide when the upper one ran Brooklyn only and skipped the exact
  boundary. Now: both values are replay-derived symbols, the probes are
  `nextafter`-adjacent and compare every case, and the prose is explicitly
  local.
- The "Pocket carry" section's first version called a tidy `(−2.6, +1.4,
  17)` synthetic control an exact reproduction of the seeded representative
  shot; the two `ImpactState` values differ by about 0.88 in laterally,
  0.08° in heading, and 0.75 mph in speed, and only happened to agree on
  the fallen set, not on the recorded crossings. It also stated the
  restitution mean/max and the restitution×radius grid as one-off numbers
  with no test behind them, while a separate section said restitution
  sensitivity was unmeasured — a direct contradiction. And its radius
  experiments varied `PIN_EFFECTIVE_RADIUS_IN` alone, leaving the
  documented coupled fall threshold at its old value, so they did not test
  the rule actually being claimed about. All three are corrected above: the
  representative case is now the real derived impact, proven equivalent to
  both HTTP routes on the complete run; the restitution and radius×grid
  claims are now executable, bounded, and reported exactly; and every
  radius experiment couples the fall threshold with it. See "Verified data,
  product assumptions, and unmeasured questions" for what is and is not
  claimed now.

## Relationship to scoring

None of this feeds scoring decisions. Which pins fell is
`fallen_pin_ids`, decided solely by the displacement threshold; the replay
and its termination reason are published observation over a run that already
happened. A client must not derive pin state, carry, or score from a
termination reason.
