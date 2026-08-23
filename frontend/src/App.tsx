import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, resetGame, throwBall } from './api/client';
import type { GameStateResponse, GameThrowResponse, ThrowRequest } from './api/types';
import styles from './App.module.css';
import { BallSelect } from './components/BallSelect';
import { LaneCanvas } from './components/LaneCanvas';
import { ReleaseControls } from './components/ReleaseControls';
import { ReleaseSeedControl } from './components/ReleaseSeedControl';
import { ScoreboardPanel } from './components/ScoreboardPanel';
import { StaleGameNotice } from './components/StaleGameNotice';
import { ThrowControls, type ThrowStatus } from './components/ThrowControls';
import { DEFAULT_BALL_ID } from './domain/ballCatalog';
import { bootstrapGame, classifyThrowFailure, describeLaneVersion, isStaleGameError, startNewGame } from './domain/gameLifecycle';
import { defaultReleaseValues, type ReleaseFieldId } from './domain/releaseFields';
import { parseReleaseSeed } from './domain/releaseSeed';
import { canReplay } from './domain/trajectoryAnimation';

interface GameSnapshot {
  gameId: string;
  gameState: GameStateResponse;
}

function messageFor(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function App() {
  const [game, setGame] = useState<GameSnapshot | null>(null);
  // These two are deliberately separate, not one "the lane version" field:
  // create/reset (and the load-time GET) report the *current* version, but
  // a throw response's lane_condition_version is documented as the version
  // that throw ran *against* (pre-wear) — see the root README's "Read a
  // game without changing it". Conflating them would show a stale number
  // as if it were current. currentLaneVersion is only ever set from a
  // response that's actually current; lastThrowRanAgainstVersion is only
  // ever set from a throw response, and labeled as exactly that.
  const [currentLaneVersion, setCurrentLaneVersion] = useState<number | null>(null);
  const [lastThrowRanAgainstVersion, setLastThrowRanAgainstVersion] = useState<number | null>(null);
  const [initError, setInitError] = useState<string | null>(null);
  const [staleGameMessage, setStaleGameMessage] = useState<string | null>(null);
  const [ballId, setBallId] = useState(DEFAULT_BALL_ID);
  const [releaseValues, setReleaseValues] = useState(defaultReleaseValues());
  const [releaseSeed, setReleaseSeed] = useState('');
  const [latestThrow, setLatestThrow] = useState<GameThrowResponse | null>(null);
  const [latestRequestedRelease, setLatestRequestedRelease] = useState<ThrowRequest | null>(null);
  const [status, setStatus] = useState<ThrowStatus>({ kind: 'idle' });
  // A request completing only means the server scored it; it does not mean
  // the player has finished seeing that ball travel and reach the deck.
  // Keep submission locked through the entire authoritative presentation.
  // The ref closes the tiny interval before React re-renders after a click,
  // so two rapid click events still produce at most one HTTP throw request.
  const [presentationLocked, setPresentationLocked] = useState(false);
  const presentationLockRef = useRef(false);
  // True from the moment an ordinary throw rejection lands (e.g. a 503
  // truncated-trajectory response) until an actual successful state
  // transition supersedes it — a new successful throw, a reset, or a new
  // game. Exists solely to keep "Replay last shot" disabled on the still-
  // displayed prior throw for that whole span; `status` alone can't do
  // this because it moves from 'loading' back to 'error' the instant the
  // request settles, which would otherwise re-enable replay immediately.
  const [throwRejected, setThrowRejected] = useState(false);

  // Guards every async setState below against firing after a real unmount.
  // Re-armed (not just initialized) inside the effect below because
  // React StrictMode's dev-only synthetic unmount/remount runs that
  // effect's cleanup — setting this false — before running the effect
  // body again, which must set it back to true or every later update
  // would be silently dropped for the rest of the component's real life.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const setPresentationLock = useCallback((locked: boolean) => {
    presentationLockRef.current = locked;
    setPresentationLocked(locked);
  }, []);

  const handlePlaybackStarted = useCallback(() => {
    setPresentationLock(true);
  }, [setPresentationLock]);

  const handlePlaybackCompleted = useCallback(() => {
    setPresentationLock(false);
  }, [setPresentationLock]);

  const runBootstrap = useCallback(() => {
    setInitError(null);
    // bootstrapGame() is memoized at module scope (see domain/gameLifecycle.ts)
    // specifically so that StrictMode invoking this effect twice on mount
    // shares one create-or-load attempt instead of racing two and orphaning
    // a game — this call site doesn't need its own de-duplication.
    bootstrapGame().then(
      (result) => {
        if (!mountedRef.current) {
          return;
        }
        setGame({ gameId: result.gameId, gameState: result.gameState });
        setCurrentLaneVersion(result.laneConditionVersion);
        setLastThrowRanAgainstVersion(null);
      },
      (error: unknown) => {
        if (!mountedRef.current) {
          return;
        }
        setInitError(messageFor(error, 'Could not start a game.'));
      },
    );
  }, []);

  useEffect(() => {
    // This *is* the rule's own sanctioned case ("synchronize with an
    // external system"): runBootstrap's setState calls are async, inside
    // bootstrapGame()'s .then()/.catch(), not synchronous work here.
    // oxlint-disable-next-line react/set-state-in-effect
    runBootstrap();
  }, [runBootstrap]);

  const handleReleaseChange = useCallback((id: ReleaseFieldId, value: number) => {
    setReleaseValues((previous) => ({ ...previous, [id]: value }));
  }, []);

  async function handleThrow() {
    if (!game || presentationLockRef.current || status.kind === 'loading') {
      return;
    }
    const requestedRelease: ThrowRequest = { ball_id: ballId, ...releaseValues };
    const parsedSeed = parseReleaseSeed(releaseSeed);
    if (parsedSeed.kind === 'invalid') {
      setStatus({ kind: 'error', message: parsedSeed.message });
      return;
    }
    if (parsedSeed.kind === 'valid') {
      requestedRelease.seed = parsedSeed.seed;
    }
    setPresentationLock(true);
    setStatus({ kind: 'loading', label: 'Throwing' });
    try {
      const response = await throwBall(game.gameId, requestedRelease);
      if (!mountedRef.current) {
        return;
      }
      setLatestThrow(response);
      setLatestRequestedRelease(requestedRelease);
      setGame({ gameId: response.game_id, gameState: response.game_state });
      setLastThrowRanAgainstVersion(response.lane_condition_version);
      setThrowRejected(false);
      const pinWord = response.pins_knocked === 1 ? 'pin' : 'pins';
      setStatus({ kind: 'success', message: `Threw it — ${response.pins_knocked} ${pinWord} down.` });
    } catch (error) {
      if (!mountedRef.current) {
        return;
      }
      // A throw's own 404 is ambiguous (missing game vs. an unrelated
      // unknown ball_id — see classifyThrowFailure's docstring), so it's
      // confirmed against a fresh GET before ever offering to discard a
      // possibly-live game. Non-404 errors pass through with no extra call.
      const classification = await classifyThrowFailure(game.gameId, error);
      if (!mountedRef.current) {
        return;
      }
      if (classification.kind === 'confirmed-missing-game') {
        setPresentationLock(false);
        setStatus({ kind: 'idle' });
        setStaleGameMessage('This game no longer exists on the server (it may have restarted).');
      } else {
        // An ordinary rejection (e.g. the solver's 503): the previously
        // completed throw stays exactly as displayed, but it must not
        // look replayable again the instant this status becomes 'error'
        // — see canReplay's docstring for why isBusy alone can't gate this.
        setThrowRejected(true);
        setPresentationLock(false);
        setStatus({ kind: 'error', message: messageFor(classification.error, 'The throw did not go through.') });
      }
    }
  }

  async function handleReset() {
    if (!game) {
      return;
    }
    setPresentationLock(true);
    setStatus({ kind: 'loading', label: 'Resetting' });
    try {
      const response = await resetGame(game.gameId);
      if (!mountedRef.current) {
        return;
      }
      setGame({ gameId: response.game_id, gameState: response.game_state });
      setCurrentLaneVersion(response.lane_condition_version);
      setLastThrowRanAgainstVersion(null);
      setLatestThrow(null);
      setLatestRequestedRelease(null);
      setThrowRejected(false);
      setPresentationLock(false);
      setStatus({ kind: 'success', message: 'Game reset — fresh rack, blank scorecard.' });
    } catch (error) {
      if (!mountedRef.current) {
        return;
      }
      if (isStaleGameError(error)) {
        setPresentationLock(false);
        setStatus({ kind: 'idle' });
        setStaleGameMessage('This game no longer exists on the server (it may have restarted).');
      } else {
        setPresentationLock(false);
        setStatus({ kind: 'error', message: messageFor(error, 'The reset did not go through.') });
      }
    }
  }

  async function handleStartNewGame() {
    setPresentationLock(true);
    setStatus({ kind: 'loading', label: 'Starting a new game' });
    try {
      const result = await startNewGame();
      if (!mountedRef.current) {
        return;
      }
      setGame({ gameId: result.gameId, gameState: result.gameState });
      setCurrentLaneVersion(result.laneConditionVersion);
      setLastThrowRanAgainstVersion(null);
      setLatestThrow(null);
      setLatestRequestedRelease(null);
      setStaleGameMessage(null);
      setThrowRejected(false);
      setPresentationLock(false);
      setStatus({ kind: 'success', message: 'Started a new game.' });
    } catch (error) {
      if (!mountedRef.current) {
        return;
      }
      // Stay in the stale state — the recovery control stays visible and
      // retryable rather than silently discarding the (already-gone) game.
      setPresentationLock(false);
      setStatus({ kind: 'error', message: messageFor(error, 'Could not start a new game.') });
    }
  }

  const isBusy = status.kind === 'loading';
  const isStale = staleGameMessage !== null;

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <h1>Bowling-Sim</h1>
        <p>A physics-simulated lane — pick a ball, set your release, and throw.</p>
      </header>

      {initError && (
        <div role="alert" className={styles.initError}>
          <p>{initError}</p>
          <button type="button" className={styles.retryButton} onClick={runBootstrap}>
            Retry
          </button>
        </div>
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
              <BallSelect value={ballId} onChange={setBallId} disabled={isBusy || isStale || presentationLocked} />
              <ReleaseControls
                values={releaseValues}
                onChange={handleReleaseChange}
                disabled={isBusy || isStale || presentationLocked}
              />
              <ReleaseSeedControl
                value={releaseSeed}
                onChange={setReleaseSeed}
                lastSeed={latestThrow?.seed ?? null}
                onUseLastSeed={() => setReleaseSeed(String(latestThrow?.seed ?? ''))}
                disabled={isBusy || isStale || presentationLocked}
              />
              {staleGameMessage && (
                <StaleGameNotice message={staleGameMessage} onStartNewGame={() => void handleStartNewGame()} disabled={isBusy} />
              )}
              <ThrowControls
                onThrow={() => void handleThrow()}
                onReset={() => void handleReset()}
                isGameComplete={game.gameState.is_game_complete}
                status={status}
                cooldown={presentationLocked}
                disabled={isStale}
              />
            </div>
          </section>

          <section aria-labelledby="lane-heading" className={styles.lanePanel}>
            <h2 id="lane-heading" className={styles.panelHeading}>
              Lane
            </h2>
            <LaneCanvas
              standingPinIds={game.gameState.standing_pin_ids}
              latestThrow={latestThrow}
              latestRequestedRelease={latestRequestedRelease}
              replayEnabled={canReplay(latestThrow, isBusy || presentationLocked, isStale, throwRejected)}
              onPlaybackStarted={handlePlaybackStarted}
              onPlaybackCompleted={handlePlaybackCompleted}
              requestPending={isBusy}
            />
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
          {describeLaneVersion({ currentLaneVersion, lastThrowRanAgainstVersion })} Game ID:{' '}
          <code>{game?.gameId ?? '—'}</code>
        </p>
      </footer>
    </div>
  );
}

export default App;
