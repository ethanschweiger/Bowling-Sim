import styles from './StaleGameNotice.module.css';

interface StaleGameNoticeProps {
  message: string;
  onStartNewGame: () => void;
  disabled?: boolean;
}

/** Shown when a throw or reset comes back 404 for the saved game — the
 * server no longer has it (most likely it restarted; games are in-memory
 * only, see the root README). The rest of the UI keeps showing the last
 * good state; this is the one recovery action, not a silent reset. */
export function StaleGameNotice({ message, onStartNewGame, disabled = false }: StaleGameNoticeProps) {
  return (
    <div className={styles.notice} role="alert">
      <p className={styles.message}>{message}</p>
      <button type="button" className={styles.button} onClick={onStartNewGame} disabled={disabled}>
        Start a new game
      </button>
    </div>
  );
}
