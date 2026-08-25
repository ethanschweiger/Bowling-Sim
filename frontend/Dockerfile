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

WORKDIR /app

# Lockfile-driven install, matching CI's `npm ci` exactly (see
# .github/workflows/ci.yml) -- never `npm install`, so a container build
# can't silently drift from the committed lockfile.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# The existing app source, not node_modules or a stale local dist/ --
# both already excluded by the root .dockerignore, but named explicitly
# here too so the intent of what belongs in the image is self-evident.
COPY frontend/index.html frontend/vite.config.ts ./
COPY frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json ./
COPY frontend/public ./public
COPY frontend/src ./src

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
