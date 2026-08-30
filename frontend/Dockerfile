# Local evaluation image for the Vite dev server. It is not a production
# build-and-serve image.
#
# node:20-slim (Debian-based, glibc) rather than an Alpine variant:
# Vite's dev server pulls in esbuild's prebuilt native binary, which
# targets glibc and is a known source of container-only breakage on
# musl-based Alpine images. Slim avoids that class of problem entirely.
#
# Built with the repository root as context (see docker-compose.yml), so
# every COPY source path below is relative to the repo root.
FROM node:20-slim

# The official node image's own existing unprivileged account -- not a
# newly created one (verified directly: node:20-slim already ships a
# `node` user at uid=1000 gid=1000, with its own writable home directory
# at /home/node, the same numeric identity the backend image's dedicated
# `backend` user already uses). Switched to before WORKDIR, so /app
# itself (created on the next line, since it doesn't exist in the base
# image) is owned by this user from the moment it's created -- letting
# `npm ci` below genuinely create node_modules/ (and Vite's own dev-time
# dependency cache under node_modules/.vite/) as this user, not needing a
# separate root-then-chown step the way the backend's pip install does.
USER node

WORKDIR /app

# Lockfile-driven install, matching CI's `npm ci` exactly (see
# .github/workflows/ci.yml) -- never `npm install`, so a container build
# can't silently drift from the committed lockfile. Runs as the `node`
# user already active (see USER above), not root -- npm needs no
# elevated privilege for a local, non-global, project-scoped install.
# --chown=node:node here and below is explicit, not incidental: even
# though /app is already node-owned from WORKDIR, this makes the intent
# unambiguous rather than relying on COPY's own default-ownership rules.
COPY --chown=node:node frontend/package.json frontend/package-lock.json ./
RUN npm ci

# The existing app source, not node_modules or a stale local dist/ --
# both already excluded by the root .dockerignore, but named explicitly
# here too so the intent of what belongs in the image is self-evident.
COPY --chown=node:node frontend/index.html frontend/vite.config.ts ./
COPY --chown=node:node frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json ./
COPY --chown=node:node frontend/public ./public
COPY --chown=node:node frontend/src ./src

EXPOSE 5173

# Probes this same Vite dev server's own root page -- confirmed directly
# (not assumed) that a plain GET / returns HTTP 200 with the real
# index.html/React-refresh bootstrap, both from the host and, more to
# the point, via Node's own fetch from inside a running container. Any
# other status, or a connection failure, counts unhealthy. Uses only
# Node's global `fetch` and `AbortSignal.timeout`, already built into
# node:20-slim's runtime (available since Node 17.3) -- no curl, no new
# package, mirroring the backend image's own urllib-only approach.
# Runs as the USER already set above (node, not root): fetch needs no
# elevated privilege to open an outbound connection to a port it isn't
# binding.
#   --interval=30s / --timeout=3s / --start-period=5s / --retries=3:
#     the same values the backend's own healthcheck already uses --
#     reused deliberately, not copied blindly: this dev server answered
#     its own root page near-instantly in direct testing, the same
#     "quick to respond even so" property the backend's own /health
#     has, so the same generous-timeout, brief-grace-window, three-
#     strikes reasoning applies here too.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:5173/', { signal: AbortSignal.timeout(3000) }).then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

# --host 0.0.0.0 is a container-only CLI flag, not a change to
# vite.config.ts or package.json's "dev" script -- normal host-machine
# `npm run dev` (which intentionally binds to localhost only) is
# unaffected. Vite's dev server must bind beyond 127.0.0.1 for Docker's
# published port mapping to reach it at all; VITE_BACKEND_ORIGIN (see
# docker-compose.yml and vite.config.ts's existing proxy target) is how
# the proxy is redirected from the host backend to the backend *service*
# without touching any source file.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
