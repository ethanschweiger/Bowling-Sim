# Local development image only -- runs the existing Vite dev server
# exactly as `README.md`'s "Run it locally" documents, inside a container
# instead of directly on the host. Not a production build/serve image.
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

# --host 0.0.0.0 is a container-only CLI flag, not a change to
# vite.config.ts or package.json's "dev" script -- normal host-machine
# `npm run dev` (which intentionally binds to localhost only) is
# unaffected. Vite's dev server must bind beyond 127.0.0.1 for Docker's
# published port mapping to reach it at all; VITE_BACKEND_ORIGIN (see
# docker-compose.yml and vite.config.ts's existing proxy target) is how
# the proxy is redirected from the host backend to the backend *service*
# without touching any source file.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
