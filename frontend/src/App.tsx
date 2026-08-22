import { useCallback, useEffect, useState } from 'react';
import { ApiError, createGame, getGame, resetGame, throwBall } from './api/client';
import type { GameStateResponse, GameThrowResponse } from './api/types';
import styles from './App.module.css';
import { BallSelect } from './components/BallSelect';
import { LaneCanvas } from './components/LaneCanvas';
import { ReleaseControls } from './components/ReleaseControls';
import { ScoreboardPanel } from './components/ScoreboardPanel';
import { ThrowControls, type ThrowStatus } from './components/ThrowControls';
import { DEFAULT_BALL_ID } from './domain/ballCatalog';
import { defaultReleaseValues, type ReleaseFieldId } from './domain/releaseFields';

const GAME_ID_STORAGE_KEY = 'bowling-sim:game-id';

interface GameSnapshot {
  gameId: string;
  laneConditionVersion: number;
  gameState: GameStateResponse;
}

function messageFor(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function App() {
  const [game, setGame] = useState<GameSnapshot | null>(null);
  const [initError, setInitError] = useState<string | null>(null);
  const [ballId, setBallId] = useState(DEFAULT_BALL_ID);
  const [releaseValues, setReleaseValues] = useState(defaultReleaseValues());
  const [latestThrow, setLatestThrow] = useState<GameThrowResponse | null>(null);
  const [status, setStatus] = useState<ThrowStatus>({ kind: 'idle' });

  // On load: resume the game this browser created last time, if the server
  // still has it, otherwise create a fresh one. Either way this is the one
  // in-memory game this tab drives — see the root README's "Read a game
  // without changing it" for what GET does and doesn't guarantee.
  useEffect(() => {
    let cancelled = false;

    async function init() {
      const storedId = window.localStorage.getItem(GAME_ID_STORAGE_KEY);
      try {
        if (storedId) {
          try {
            const found = await getGame(storedId);
            if (!cancelled) {
              setGame({
                gameId: found.game_id,
                laneConditionVersion: found.lane_condition_version,
                gameState: found.game_state,
              });
            }
            return;
          } catch (error) {
            if (!(error instanceof ApiError && error.status === 404)) {
              throw error;
            }
            // Stored game_id is stale (server restarted, or it never
            // existed) — fall through and create a new one below.
          }
        }

        const created = await createGame();
        window.localStorage.setItem(GAME_ID_STORAGE_KEY, created.game_id);
        if (!cancelled) {
          setGame({
            gameId: created.game_id,
            laneConditionVersion: created.lane_condition_version,
            gameState: created.game_state,
          });
        }
      } catch (error) {
        if (!cancelled) {
          setInitError(messageFor(error, 'Could not start a game.'));
        }
      }
    }

    void init();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleReleaseChange = useCallback((id: ReleaseFieldId, value: number) => {
    setReleaseValues((previous) => ({ ...previous, [id]: value }));
  }, []);

  async function handleThrow() {
    if (!game) {
      return;
    }
    setStatus({ kind: 'loading', label: 'Throwing' });
    try {
      const response = await throwBall(game.gameId, { ball_id: ballId, ...releaseValues });
      setLatestThrow(response);
      setGame({
        gameId: response.game_id,
        laneConditionVersion: response.lane_condition_version,
        gameState: response.game_state,
      });
      const pinWord = response.pins_knocked === 1 ? 'pin' : 'pins';
      setStatus({ kind: 'success', message: `Threw it — ${response.pins_knocked} ${pinWord} down.` });
    } catch (error) {
      setStatus({ kind: 'error', message: messageFor(error, 'The throw did not go through.') });
    }
  }

  async function handleReset() {
    if (!game) {
      return;
    }
    setStatus({ kind: 'loading', label: 'Resetting' });
    try {
      const response = await resetGame(game.gameId);
      setGame({
        gameId: response.game_id,
        laneConditionVersion: response.lane_condition_version,
        gameState: response.game_state,
      });
      setLatestThrow(null);
      setStatus({ kind: 'success', message: 'Game reset — fresh rack, blank scorecard.' });
    } catch (error) {
      setStatus({ kind: 'error', message: messageFor(error, 'The reset did not go through.') });
    }
  }

  const isBusy = status.kind === 'loading';

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <h1>Bowling-Sim</h1>
        <p>A physics-simulated lane — pick a ball, set your release, and throw.</p>
      </header>

      {initError && (
        <p role="alert" className={styles.initError}>
          {initError}
        </p>
      )}
      {!game && !initError && (
        <p aria-live="polite" className={styles.loadingText}>
          Loading your game…
        </p>
      )}

      {game && (
        <main className={styles.main}>
          <section aria-labelledby="controls-heading" className={styles.controlsPanel}>
            <h2 id="controls-heading" className={styles.panelHeading}>
              Set up your throw
            </h2>
            <div className={styles.controlsStack}>
              <BallSelect value={ballId} onChange={setBallId} disabled={isBusy} />
              <ReleaseControls values={releaseValues} onChange={handleReleaseChange} disabled={isBusy} />
              <ThrowControls
                onThrow={() => void handleThrow()}
                onReset={() => void handleReset()}
                isGameComplete={game.gameState.is_game_complete}
                status={status}
              />
            </div>
          </section>

          <section aria-labelledby="lane-heading" className={styles.lanePanel}>
            <h2 id="lane-heading" className={styles.panelHeading}>
              Lane
            </h2>
            <LaneCanvas standingPinIds={game.gameState.standing_pin_ids} latestThrow={latestThrow} />
          </section>

          <section aria-labelledby="scorecard-heading" className={styles.scorePanel}>
            <h2 id="scorecard-heading" className={styles.panelHeading}>
              Scorecard
            </h2>
            <ScoreboardPanel gameState={game.gameState} />
          </section>
        </main>
      )}

      <footer className={styles.footer}>
        <p>
          Lane condition version {game?.laneConditionVersion ?? '—'}. Game ID: <code>{game?.gameId ?? '—'}</code>
        </p>
      </footer>
    </div>
  );
}

export default App;
