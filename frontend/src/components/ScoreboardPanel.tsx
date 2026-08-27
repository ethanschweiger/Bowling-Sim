import type { GameStateResponse } from '../api/types';
import { formatScore, frameCellSymbols } from '../domain/scoreDisplay';
import styles from './ScoreboardPanel.module.css';

const FRAME_NUMBERS = Array.from({ length: 10 }, (_, i) => i + 1);

interface ScoreboardPanelProps {
  gameState: GameStateResponse;
  /** The loaded game's oil pattern, already resolved to a display name --
   * see `domain/oilPatternCatalog.ts`'s `oilPatternDisplayName` for how
   * the caller derives this (the server catalog's name when available,
   * falling back to the raw `gameState.oil_pattern` id otherwise). This
   * component only renders the string it's given; it does not know about
   * the catalog itself. */
  oilPatternName: string;
}

/** The ten-frame scoresheet. Every cell is either a frame the server has
 * already reported (rendered from its `rolls`/`score`) or a not-yet-played
 * frame (a plain dash) — there's no separate "empty state" component,
 * because a brand new game's `frames: []` is just this same table with
 * all ten columns in that not-yet-played state. */
export function ScoreboardPanel({ gameState, oilPatternName }: ScoreboardPanelProps) {
  const frameByNumber = new Map(gameState.frames.map((frame) => [frame.number, frame]));

  return (
    <div className={styles.wrapper}>
      <p className={styles.oilPatternLabel}>
        Lane: <span className={styles.oilPatternValue}>{oilPatternName}</span>
      </p>
      <div className={styles.tableScroll}>
        <table className={styles.table}>
          <caption className={styles.caption}>Scorecard</caption>
          <thead>
            <tr>
              <th scope="col" className={styles.rowLabel}>
                Frame
              </th>
              {FRAME_NUMBERS.map((number) => (
                <th
                  scope="col"
                  key={number}
                  className={number === gameState.next_frame_number ? styles.currentColumn : undefined}
                >
                  {number}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row" className={styles.rowLabel}>
                Rolls
              </th>
              {FRAME_NUMBERS.map((number) => {
                const frame = frameByNumber.get(number);
                const isCurrent = number === gameState.next_frame_number;
                return (
                  <td key={number} className={isCurrent ? styles.currentColumn : undefined}>
                    {frame ? (
                      <span className={styles.rolls}>
                        {frameCellSymbols(frame).map((symbol, index) => (
                          <span className={styles.rollChip} key={index}>
                            {symbol}
                          </span>
                        ))}
                      </span>
                    ) : (
                      <>
                        <span aria-hidden="true" className={styles.emptyCell}>
                          –
                        </span>
                        <span className="sr-only">not yet played</span>
                      </>
                    )}
                  </td>
                );
              })}
            </tr>
            <tr>
              <th scope="row" className={styles.rowLabel}>
                Score
              </th>
              {FRAME_NUMBERS.map((number) => {
                const frame = frameByNumber.get(number);
                const isCurrent = number === gameState.next_frame_number;
                return (
                  <td key={number} className={`${styles.scoreCell} ${isCurrent ? styles.currentColumn : ''}`}>
                    {frame ? formatScore(frame.score) : <span className={styles.emptyCell}>–</span>}
                  </td>
                );
              })}
            </tr>
          </tbody>
        </table>
      </div>
      <div className={styles.totalRow}>
        <span className={styles.totalLabel}>Total score</span>
        <span className={styles.totalValue}>{formatScore(gameState.total_score)}</span>
        {gameState.is_game_complete && <span className={styles.completeBadge}>Game complete</span>}
      </div>
    </div>
  );
}
