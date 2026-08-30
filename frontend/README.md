# Bowling-Sim frontend

A Vite + React + TypeScript client for the Bowling-Sim API. It renders the
server-recorded trajectory, rack, and scorecard without recomputing physics or
game state. See the root [`README.md`](../README.md#architecture) for the system
boundary and [`docs/testing.md`](../docs/testing.md#native-development) for
native setup.

With the backend already running on `127.0.0.1:8000`:

```bash
npm ci
npm run dev
```

```bash
npm run build   # TypeScript check + production build
npm run test    # vitest
npm run lint    # oxlint
```
