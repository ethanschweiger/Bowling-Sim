# Limitations

## Physical model

- Pinfall is a planar circle-collision approximation. It does not model pin tilt
  or rotation in 3D, loft, kickbacks, string-pinsetter behavior, or placement
  variance.
- Collision radius, damping, and fall threshold are calibration choices rather
  than values fitted to observed pinfall data. Exact leaves and strike rates are
  not real-world predictions.
- The pocket convention assumes a right-handed line; there is no mirrored
  handedness mode.
- Trajectory coefficients are hand-tuned for bounded, explainable motion, not
  fitted to tracked ball-motion data. Launch angle remains a sustained heading
  in the planar model and is therefore restricted to ±2°.
- `mass_lbs` does not affect the Coulomb-friction trajectory because mass cancels
  from acceleration. Catalog balls share a regulation radius. Mass still enters
  the downstream impact calculation.
- Oil-pattern dimensions, volume, ratios, lane-temperature adjustment, and
  release-error bounds are documented modeling assumptions. The bundled
  patterns are not certified lane programs.
- Canvas board spacing and the final downlane section are exaggerated for
  legibility. Playback is scaled from server-recorded time and is not literal
  real-time video.

## Product model

- A game's oil pattern is fixed at creation. Reset restores that same pattern;
  there is no editor or mid-game switch.
- There are no accounts, authentication, ownership checks, multiplayer turn
  enforcement, analytics, or background synchronization.
- The deprecated single-lane endpoint shares one game across callers.
- The frontend loads catalogs at startup and detects a lost game on load or the
  next mutation, not through continuous polling.

## Storage and deployment

- Memory mode loses games on backend restart and evicts the oldest-created game
  after the 1,000-session cap.
- SQL migrations are explicit. Health verifies connectivity, not schema version.
- Same-game coordination is process-local. Multiple API worker processes need a
  database row version/optimistic transaction before they can safely mutate the
  same SQL-backed game concurrently.
- The Compose files are a local evaluation environment. They do not provide TLS,
  backups, replication, user isolation, rate limiting, or production serving of
  the frontend.
