import styles from './ReleaseSeedControl.module.css';

interface ReleaseSeedControlProps {
  value: string;
  onChange: (value: string) => void;
  lastSeed: number | null;
  onUseLastSeed: () => void;
  disabled?: boolean;
}

/** Optional request seed. It never generates randomness in the browser:
 * blank means the backend generates and returns a seed, while a typed or
 * reused value goes straight back in the next throw request. */
export function ReleaseSeedControl({
  value,
  onChange,
  lastSeed,
  onUseLastSeed,
  disabled = false,
}: ReleaseSeedControlProps) {
  const inputId = 'release-seed';
  const helpId = `${inputId}-help`;

  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={inputId}>
        Release seed (optional)
      </label>
      <input
        id={inputId}
        className={styles.input}
        type="number"
        min="0"
        max="2147483647"
        step="1"
        inputMode="numeric"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby={helpId}
      />
      <p className={styles.help} id={helpId}>
        Leave blank for a new sampled release. Reuse a returned seed to repeat that release exactly.
      </p>
      {lastSeed !== null && (
        <button type="button" className={styles.reuseButton} onClick={onUseLastSeed} disabled={disabled}>
          Use last seed ({lastSeed})
        </button>
      )}
    </div>
  );
}
