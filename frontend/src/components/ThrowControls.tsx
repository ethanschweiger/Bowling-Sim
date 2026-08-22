import styles from './ThrowControls.module.css';

export type ThrowStatus =
  | { kind: 'idle' }
  | { kind: 'loading'; label: string }
  | { kind: 'error'; message: string }
  | { kind: 'success'; message: string };

interface ThrowControlsProps {
  onThrow: () => void;
  onReset: () => void;
  isGameComplete: boolean;
  status: ThrowStatus;
}

/** The primary throw/reset actions, plus one status region that announces
 * in-progress requests and errors to assistive tech (`role="alert"` for
 * errors — interrupts immediately; `aria-live="polite"` otherwise, so
 * routine updates don't talk over whatever the user is doing). */
export function ThrowControls({ onThrow, onReset, isGameComplete, status }: ThrowControlsProps) {
  const isLoading = status.kind === 'loading';

  return (
    <div>
      <div className={styles.buttonRow}>
        <button
          type="button"
          className={`${styles.button} ${styles.primary}`}
          onClick={onThrow}
          disabled={isLoading || isGameComplete}
        >
          {isLoading && status.label === 'Throwing' ? 'Throwing…' : 'Throw'}
        </button>
        <button
          type="button"
          className={`${styles.button} ${styles.secondary}`}
          onClick={onReset}
          disabled={isLoading}
        >
          {isLoading && status.label === 'Resetting' ? 'Resetting…' : 'Reset game'}
        </button>
      </div>
      <p
        className={`${styles.status} ${statusClassName(status)}`}
        role={status.kind === 'error' ? 'alert' : undefined}
        aria-live={status.kind === 'error' ? undefined : 'polite'}
      >
        {statusMessage(status, isGameComplete)}
      </p>
    </div>
  );
}

function statusClassName(status: ThrowStatus): string {
  switch (status.kind) {
    case 'error':
      return styles.statusError;
    case 'success':
      return styles.statusSuccess;
    default:
      return styles.statusIdle;
  }
}

function statusMessage(status: ThrowStatus, isGameComplete: boolean): string {
  switch (status.kind) {
    case 'loading':
      return `${status.label}…`;
    case 'error':
      return status.message;
    case 'success':
      return status.message;
    case 'idle':
    default:
      return isGameComplete ? 'Game complete — press Reset game to play again.' : 'Ready.';
  }
}
