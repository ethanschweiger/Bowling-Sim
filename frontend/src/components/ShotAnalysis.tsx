import type { GameThrowResponse, ThrowRequest } from '../api/types';
import { shotAnalysisRows } from '../domain/shotAnalysis';
import styles from './ShotAnalysis.module.css';

interface ShotAnalysisProps {
  latestThrow: GameThrowResponse | null;
  requestedRelease: ThrowRequest | null;
}

export function ShotAnalysis({ latestThrow, requestedRelease }: ShotAnalysisProps) {
  if (!latestThrow || !requestedRelease) {
    return null;
  }

  return (
    <details className={styles.details}>
      <summary className={styles.summary}>Shot details</summary>
      <dl className={styles.rows}>
        {shotAnalysisRows(latestThrow, requestedRelease).map((row) => (
          <div className={styles.row} key={row.label}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
