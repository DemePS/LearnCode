# syntax=docker/dockerfile:1
#
# LearnCode, one image serving both halves: the React SPA (built here, into frontend/dist) and the
# FastAPI backend that serves it from the same origin (see backend/app/main.py's SPA mount).
#
# Build from the project root (the folder holding backend/ and frontend/):
#   docker build -t learncode .
# or, at the same level, `docker compose up -d --build`.

# ---------------------------------------------------------------- stage 1: the SPA
FROM node:22-slim AS frontend
WORKDIR /ui
# package files first: the dependency layer is cached until they change.
COPY frontend/package.json frontend/package-lock.json ./
# `npm ci` installs from the lockfile and fails if the two have drifted, which is what a release build
# wants. `npm run build` runs `tsc -b` before vite (see package.json), so a type error fails the image.
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- stage 2: the backend
FROM python:3.12-slim

# git             seedagent is installed from a git branch (see backend/pyproject.toml's
#                 [tool.uv.sources]) and the agent clones learners' repositories at runtime, so the
#                 binary is needed at build *and* at run time.
# curl            the HEALTHCHECK below.
# ca-certificates GitHub, the model API and every clone are https.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Dependencies first, from the lockfile only, so a code change does not reinstall them. --no-dev keeps
# pytest and its plugins out of the runtime image; --no-install-project because the project files are
# copied on the next line and are imported from the working directory, not installed.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./
# The built SPA goes where app/main.py looks for it by default:
# `Path(__file__).parent.parent.parent / "frontend" / "dist"`, i.e. one level above backend/ -- here
# /frontend/dist. LEARNCODE_FRONTEND_DIST below makes that explicit rather than relying on it.
COPY --from=frontend /ui/dist /frontend/dist

# Every write goes under /data (workspaces, the SQLite file, the GitHub cache). Mount a volume there in
# production: without one, every learner's project is lost when the container is replaced.
ENV LEARNCODE_DATA_DIR=/data \
    LEARNCODE_FRONTEND_DIST=/frontend/dist \
    LEARNCODE_HOST=0.0.0.0 \
    LEARNCODE_PORT=8000

# Run as a non-root user. The app clones third-party repositories and writes what it finds there, so it
# is the process most worth containing.
RUN groupadd --system learncode \
    && useradd --system --gid learncode --create-home learncode \
    && mkdir -p /data \
    && chown -R learncode:learncode /data /app
USER learncode

VOLUME ["/data"]
EXPOSE 8000

# /health touches the database on purpose: a process that started but cannot open its store is not
# healthy. start-period is generous because the first request builds the schema.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${LEARNCODE_PORT}/health" || exit 1

# Shell form so ${LEARNCODE_PORT} expands; exec so uvicorn (not the shell) is PID 1 and receives SIGTERM
# directly -- a shell in between swallows it and every stop becomes a 10s kill.
#
# --workers 1 is not a tuning choice. The progress feed (backend/app/progress.py), the per-session turn
# locks (backend/app/routes.py) and the rate-limit windows (backend/app/ratelimit.py) all live in
# process memory, so a turn id registered in one worker is unknown to the others: with two workers the
# UI's progress poll would land on the wrong process and the turn would look dead. Scale with more
# *containers* behind a load balancer once those three have a shared store -- see TODO.md.
CMD ["sh", "-c", "exec uvicorn app.main:app --host \"$LEARNCODE_HOST\" --port \"$LEARNCODE_PORT\" --proxy-headers --forwarded-allow-ips='*' --workers 1"]
