# API

The FastAPI schema is available at `http://localhost:8000/docs` while the backend
is running. All current endpoints are under `/api/v1` except `/health`.

## Catalogs

```bash
curl http://localhost:8000/api/v1/balls
curl http://localhost:8000/api/v1/oil-patterns
```

These responses are the authority for valid `ball_id` and `oil_pattern` values.
The frontend consumes the same catalogs rather than maintaining copies.

## Create and read a game

```bash
curl -X POST http://localhost:8000/api/v1/games \
  -H 'Content-Type: application/json' \
  -d '{"oil_pattern":"house"}'

curl http://localhost:8000/api/v1/games/{game_id}
```

`oil_pattern` is optional and accepts `house` or `challenge`. The read response
contains lane version, standing pins, scorecard frames, current total, completion
state, and the next roll position.

## Throw

```bash
curl -X POST http://localhost:8000/api/v1/games/{game_id}/throws \
  -H 'Content-Type: application/json' \
  -d '{
    "ball_id":"reactive_pearl",
    "seed":42,
    "speed_mph":17,
    "rev_rate":350,
    "axis_rotation":45,
    "axis_tilt":15,
    "launch_angle":-1.5,
    "launch_position":28
  }'
```

`seed` is optional. The response returns the seed used, sampled release,
trajectory with elapsed time, entry state, fallen pins, and the authoritative
post-throw game snapshot. A completed game rejects another throw with `409`
without changing state.

## Reset

```bash
curl -X POST http://localhost:8000/api/v1/games/{game_id}/reset
```

Reset restores the game's original oil pattern, a blank scorecard, and all ten
pins. Other games are unaffected.

## Health

```bash
curl http://localhost:8000/health
```

Memory mode returns `{"status":"ok"}`. SQL mode returns database status and uses
HTTP 503 when the connectivity check fails.

## Deprecated route

`POST /api/v1/simulations/throws` remains for backward compatibility. Every
caller shares the `legacy-default` game; new clients should use game-scoped
routes.
