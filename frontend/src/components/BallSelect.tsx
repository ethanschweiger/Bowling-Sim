import type { ChangeEvent } from 'react';
import type { BallResponse } from '../api/types';
import styles from './BallSelect.module.css';

interface BallSelectProps {
  /** The server's catalog, in the order it published. This component
   * renders whatever it is given and knows no ball ids of its own. */
  options: readonly BallResponse[];
  value: string;
  onChange: (ballId: string) => void;
  disabled?: boolean;
}

/** Ball choice, plus a fixed, non-interactive readout of the oil pattern —
 * "house" is the only pattern this milestone supports, so it's presented
 * as a fact, not a selector implying there's anything else to pick.
 *
 * Help text comes from the server's `description` for the selected ball,
 * so a ball this build has never heard of still renders correctly. */
export function BallSelect({ options, value, onChange, disabled = false }: BallSelectProps) {
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
      <p className={styles.patternInfo}>
        <strong>Oil pattern:</strong> House Shot — the only pattern available this milestone.
      </p>
    </div>
  );
}
