import type { ChangeEvent } from 'react';
import { BALL_CATALOG } from '../domain/ballCatalog';
import styles from './BallSelect.module.css';

interface BallSelectProps {
  value: string;
  onChange: (ballId: string) => void;
  disabled?: boolean;
}

/** Ball choice, plus a fixed, non-interactive readout of the oil pattern —
 * "house" is the only pattern this milestone supports, so it's presented
 * as a fact, not a selector implying there's anything else to pick. */
export function BallSelect({ value, onChange, disabled = false }: BallSelectProps) {
  const selected = BALL_CATALOG.find((ball) => ball.id === value) ?? BALL_CATALOG[0];

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
          {BALL_CATALOG.map((ball) => (
            <option key={ball.id} value={ball.id}>
              {ball.name}
            </option>
          ))}
        </select>
        <p className={styles.help} id="ball-select-help">
          {selected.description}
        </p>
      </div>
      <p className={styles.patternInfo}>
        <strong>Oil pattern:</strong> House Shot — the only pattern available this milestone.
      </p>
    </div>
  );
}
