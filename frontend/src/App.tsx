import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, resetGame, throwBall } from './api/client';
import type { BallResponse, GameStateResponse, GameThrowResponse, OilPatternResponse, ThrowRequest } from './api/types';
import styles from './App.module.css';
import { BallSelect } from './components/BallSelect';
import { LaneCanvas } from './components/LaneCanvas';
import { OilPatternSelect } from './components/OilPatternSelect';
import { ReleaseControls } from './components/ReleaseControls';
import { ReleaseSeedControl } from './components/ReleaseSeedControl';
import { ScoreboardPanel } from './components/ScoreboardPanel';
import { StaleGameNotice } from './components/StaleGameNotice';
import { ThrowControls, type ThrowStatus } from './components/ThrowControls';
import { fetchBallCatalog, isSelectable, pickDefaultBallId } from './domain/ballCatalog';
import {
  canPlayLoadedGame,
  fetchOilPatternCatalog,
  isOilPatternSelectable,
  oilPatternIdForNewGame,
  pickDefaultOilPatternId,
} from './domain/oilPatternCatalog';
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
  // The server owns the ball list, so both of these start empty and are
  // only ever filled from a catalog response. `ballId` stays null until
  // then: there is no local default to fall back on, because a hardcoded
  // id could disagree with the server and 404 on the throw.
  const [ballCatalog, setBallCatalog] = useState<BallResponse[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [ballId, setBallId] = useState<string | null>(null);
  // Same reasoning as the ball catalog above: server-owned, no local
  // fallback, starts empty until a catalog response fills it. `oilPatternId`
  // is only ever read when a *new* game is created (see handleStartNewGame)
  // — it has no effect on the currently active game.
  const [oilPatternCatalog, setOilPatternCatalog] = useState<OilPatternResponse[] | null>(null);
  const [oilPatternError, setOilPatternError] = useState<string | null>(null);
  const [oilPatternId, setOilPatternId] = useState<string | null>(null);
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

  const runCatalogLoad = useCallback(() => {
    setCatalogError(null);
    // fetchBallCatalog() is memoized at module scope (see
    // domain/ballCatalog.ts), so StrictMode's double mount and any later
    // re-render share one request rather than issuing another.
    fetchBallCatalog().then(
      (balls) => {
        if (!mountedRef.current) {
          return;
        }
        setBallCatalog(balls);
        // Only ever select an id the server actually returned.
        setBallId((current) => (isSelectable(balls, current) ? current : pickDefaultBallId(balls)));
      },
      (error: unknown) => {
        if (!mountedRef.current) {
          return;
        }
        // Named explicitly: the game bootstrap can fail at the same
        // moment for the same reason, and two identical alerts would not
        // tell the player which Retry does what.
        setCatalogError(`Could not load the ball catalog. ${messageFor(error, 'Please try again.')}`);
      },
    );
  }, []);

  useEffect(() => {
    // Same sanctioned case as the bootstrap effect above.
    // oxlint-disable-next-line react/set-state-in-effect
    runCatalogLoad();
  }, [runCatalogLoad]);

  const runOilPatternLoad = useCallback(() => {
    setOilPatternError(null);
    // fetchOilPatternCatalog() is memoized at module scope (see
    // domain/oilPatternCatalog.ts), for the same StrictMode reason as the
    // ball catalog and game bootstrap above.
    fetchOilPatternCatalog().then(
      (patterns) => {
        if (!mountedRef.current) {
          return;
        }
        setOilPatternCatalog(patterns);
        // Only ever select an id the server actually returned.
        setOilPatternId((current) =>
          isOilPatternSelectable(patterns, current) ? current : pickDefaultOilPatternId(patterns),
        );
      },
      (error: unknown) => {
        if (!mountedRef.current) {
          return;
        }
        // Named for the same reason the ball-catalog error is: two
        // identical alerts would not tell the player which Retry does what.
        setOilPatternError(`Could not load the oil pattern catalog. ${messageFor(error, 'Please try again.')}`);
      },
    );
  }, []);

  useEffect(() => {
    // Same sanctioned case as the bootstrap effect above.
    // oxlint-disable-next-line react/set-state-in-effect
    runOilPatternLoad();
  }, [runOilPatternLoad]);

  const handleReleaseChange = useCallback((id: ReleaseFieldId, value: number) => {
    setReleaseValues((previous) => ({ ...previous, [id]: value }));
  }, []);

  async function handleThrow() {
    if (!game || presentationLockRef.current || status.kind === 'loading') {
      return;
    }
    // Never submit a ball the server did not publish. `ballId` is only
    // ever set from a catalog response, so this is a guard against a
    // catalog that failed to load rather than an expected branch.
    if (!ballCatalog || ballId === null || !isSelectable(ballCatalog, ballId)) {
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
      // Only sent when it's actually one the server published; otherwise
      // omitted so the server falls back to its own default ("house"),
      // exactly like the initial bootstrap create already does. This
      // covers a failed catalog load (`oilPatternCatalog === null`), so
      // new-game recovery stays usable in that state.
      const result = await startNewGame(
        undefined,
        oilPatternIdForNewGame(oilPatternCatalog, oilPatternId),
      );
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
  // Gameplay needs exactly two things: the game itself and the server's
  // ball list. The oil-pattern catalog is deliberately excluded — see
  // `canPlayLoadedGame`, which owns that rule and its regression test.
  //
  // Route any future change to this readiness rule through
  // `canPlayLoadedGame` (and its test) rather than inlining a new
  // condition here or on the `<main>` render guard below: this repo has
  // no component-render test infrastructure, so nothing would catch a
  // regression re-inlined directly at either of those two spots instead
  // of going through the function.
  const isReady = canPlayLoadedGame(game !== null, ballCatalog !== null);

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
      {catalogError && (
        <div role="alert" className={styles.initError}>
          <p>{catalogError}</p>
          <button type="button" className={styles.retryButton} onClick={runCatalogLoad}>
            Retry
          </button>
        </div>
      )}
      {oilPatternError && (
        <div role="alert" className={styles.initError}>
          <p>{oilPatternError}</p>
          <button type="button" className={styles.retryButton} onClick={runOilPatternLoad}>
            Retry
          </button>
        </div>
      )}
      {/* Not gated on oilPatternError: that failure leaves the game fully
          playable, so it must not keep showing "Loading your game…". */}
      {!isReady && !initError && !catalogError && (
        <p aria-live="polite" className={styles.loadingText}>
          Loading your game…
        </p>
      )}

      {isReady && game && ballCatalog && (
        <main className={styles.main}>
          <section aria-labelledby="controls-heading" className={styles.controlsPanel}>
            <h2 id="controls-heading" className={styles.panelHeading}>
              Set up your throw
            </h2>
            <div className={styles.controlsStack}>
              <BallSelect
                options={ballCatalog}
                value={ballId ?? ''}
                onChange={setBallId}
                disabled={isBusy || isStale || presentationLocked}
              />
              {/* Omitted entirely when the catalog didn't load: there is
                  nothing truthful to offer, and the error banner above
                  already carries the failure and its Retry. A new game
                  started in this state simply omits `oil_pattern` and
                  gets the server's "house" default. */}
              {oilPatternCatalog && (
                <OilPatternSelect
                  options={oilPatternCatalog}
                  value={oilPatternId ?? ''}
                  onChange={setOilPatternId}
                  disabled={isBusy || presentationLocked}
                />
              )}
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
