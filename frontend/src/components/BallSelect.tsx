import type { ChangeEvent } from 'react';
import type { BallResponse, OilPatternResponse } from '../api/types';
import styles from './BallSelect.module.css';

interface BallSelectProps {
  /** The server's catalog, in the order it published. This component
   * renders whatever it is given and knows no ball ids of its own. */
  options: readonly BallResponse[];
  value: string;
  onChange: (ballId: string) => void;
  /** The pattern the non-interactive oil-pattern notice describes,
   * server-owned. Null while it hasn't loaded yet — the notice renders
   * nothing rather than a stale or invented pattern in that case. */
  pattern: OilPatternResponse | null;
  disabled?: boolean;
}

/** Ball choice, plus a fixed, non-interactive readout of the oil pattern.
 * Only one pattern is selectable this milestone, so it's presented as a
 * fact rather than a selector implying there's anything else to pick —
 * but its name and description are exactly what `pattern` carries, never
 * a locally hardcoded copy.
 *
 * Help text for the ball comes from the server's `description` for the
 * selected ball, so a ball this build has never heard of still renders
 * correctly. */
export function BallSelect({ options, value, onChange, pattern, disabled = false }: BallSelectProps) {
  const selected = options.find((ball) => ball.id === value) ?? options[0];

  function handleChange(event: ChangeEvent<HTMLSelectElement>) {
    onChange(event.target.value);
  }

  return (
    <div>
      <div className={styles.field}>
        <label className={styles.label} htmlFor="ball-select">
          Ball
        </label>
        <select id="ball-select" className={styles.select} value={value} onChange={handleChange} disabled={disabled}>
          {options.map((ball) => (
            <option key={ball.id} value={ball.id}>
              {ball.name}
            </option>
          ))}
        </select>
        <p className={styles.help} id="ball-select-help">
          {selected?.description ?? ''}
        </p>
      </div>
      {pattern && (
        <div className={styles.patternInfo}>
          <p className={styles.patternInfoName}>
            <strong>Oil pattern:</strong> {pattern.name} — the only pattern available this milestone.
          </p>
          <p className={styles.help}>{pattern.description}</p>
        </div>
      )}
    </div>
  );
}
