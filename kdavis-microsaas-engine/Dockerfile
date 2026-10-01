# mse-api production image.
#
# This is the SINGLE source of truth for how the service starts. Decided
# 2026-10-01 after a builder migration took production down: the repo had
# four competing config sources (railway.json, railpack.json, Procfile, and
# Railway service-level overrides) naming three different start commands,
# and the effective builder was not the one any file claimed. A Dockerfile
# removes the ambiguity -- the image itself carries the command, and
# Railway's service-level builder/startCommand overrides are cleared.
#
# See docs/railway-start-command.md for the full incident write-up.

FROM python:3.11-slim

# git matches the runtime package set the previous Railpack image had
# (RAILPACK_DEPLOY_APT_PACKAGES=git). No runtime shell-out to git was found
# in this codebase, but keeping the runtime environment equivalent avoids
# stacking an unproven behavioural change on top of a builder migration.
# Remove it deliberately, in its own change, if it proves unused.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code-only change does not reinstall them.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code. .dockerignore keeps frontend/ (786MB), venv/ and the
# git history out. supabase/migrations/ MUST be present -- api/main.py's
# lifespan runs run_pending_migrations() against them before the app serves
# a single request, so an image without them boots into a database whose
# schema it cannot guarantee.
COPY . .

# Run unprivileged. Done after COPY so the layer ordering above still
# caches, and /app is left readable rather than owned-and-writable.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# `python -m uvicorn` rather than bare `uvicorn`: it does not depend on the
# console script being on PATH, which is exactly what broke the Railpack
# attempt. Shell form so ${PORT} expands -- Railway injects PORT, and the
# default keeps `docker run -p 8000:8000` working locally with no env.
CMD ["sh", "-c", "python -m uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
