import type { ChangeEvent } from 'react';
import type { OilPatternResponse } from '../api/types';
import styles from './OilPatternSelect.module.css';

interface OilPatternSelectProps {
  /** The server's catalog, in the order it published. This component
   * renders whatever it is given and knows no pattern ids of its own. */
  options: readonly OilPatternResponse[];
  value: string;
  onChange: (patternId: string) => void;
  disabled?: boolean;
}

/**
 * Oil-pattern choice for the *next* new game.
 *
 * This never claims to describe the currently active game's own lane
 * condition — `GameStateResponse` doesn't echo which pattern a game was
 * created with, so the client has no way to know that once play has
 * started. What this selects only takes effect the next time a new game
 * is actually created (see `domain/gameLifecycle.ts`'s `startNewGame`);
 * it has no effect on the live game's rack, scoring, or lane state.
 *
 * Renders exactly the server's catalog, in its declared order — never a
 * hardcoded pattern id, and never a description this component invented.
 */
export function OilPatternSelect({ options, value, onChange, disabled = false }: OilPatternSelectProps) {
  const selected = options.find((pattern) => pattern.id === value) ?? options[0];

  function handleChange(event: ChangeEvent<HTMLSelectElement>) {
    onChange(event.target.value);
  }

  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor="oil-pattern-select">
        Oil pattern (next game)
      </label>
      <select
        id="oil-pattern-select"
        className={styles.select}
        value={value}
        onChange={handleChange}
        disabled={disabled}
      >
        {options.map((pattern) => (
          <option key={pattern.id} value={pattern.id}>
            {pattern.name}
          </option>
        ))}
      </select>
      <p className={styles.help} id="oil-pattern-select-help">
        {selected?.description ?? ''}
      </p>
    </div>
  );
}
