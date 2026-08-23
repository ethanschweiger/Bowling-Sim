/**
 * The four ball IDs `backend/app/physics/ball.py`'s `BALL_CATALOG` defines
 * today, with a short, honest description of each (paraphrased from that
 * module's own coverstock comments — not a new claim about ball behavior).
 *
 * This is a deliberately isolated, hardcoded UI catalog, not a guess at
 * future ones: there is no `GET /api/v1/balls` yet, so the frontend has no
 * way to ask the server what balls exist. When that endpoint arrives, this
 * file is the one place that needs to change — swap this constant for a
 * fetched list — and nothing else in the UI should need to know the
 * difference, since every component here only depends on `BallOption`.
 */

export interface BallOption {
  id: string;
  name: string;
  description: string;
}

export const BALL_CATALOG: readonly BallOption[] = [
  {
    id: 'house_ball',
    name: 'House Ball',
    description: 'Plastic coverstock, polished. Near-zero hook — predictable and straight.',
  },
  {
    id: 'urethane_smooth',
    name: 'Smooth Urethane',
    description: 'Urethane coverstock. A smooth, predictable arc with moderate hook.',
  },
  {
    id: 'reactive_pearl',
    name: 'Reactive Pearl',
    description: 'Reactive coverstock. Strong, sudden backend motion.',
  },
  {
    id: 'particle_beast',
    name: 'Particle Beast',
    description: 'Particle coverstock — reactive plus grit. Built for heavy oil.',
  },
];

// The starter release is a conventional right-handed house-shot line. A
// reactive ball makes the displayed skid-to-hook shape legible; the House
// Ball remains available for straight spare attempts.
export const DEFAULT_BALL_ID = 'reactive_pearl';
