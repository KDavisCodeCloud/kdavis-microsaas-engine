# mse-api build + deploy configuration

_Last updated 2026-10-01. Standardising on a Dockerfile; see Status._

## Decision

**The Dockerfile is the single source of truth for how this service starts.**
`CMD` carries the start command; Railway's service-level `builder` and
`startCommand` overrides are cleared. `railpack.json` and `Procfile` are
deleted.

Reason: the repo previously had FOUR config sources —`railway.json`,
`railpack.json`, `Procfile`, and Railway service-level overrides — naming
three different start commands, and the *effective* builder was not the one
any file claimed. Reconciling them took five attempts and caused a ten-minute
502. A Dockerfile removes the ambiguity: the image itself carries the command,
and there is nothing left to disagree with.

## Status

| Step | State |
|---|---|
| `Dockerfile` + `.dockerignore` written | ✅ done |
| Build and run the image locally | ⛔ **blocked** — Docker Desktop's Linux engine is not reachable from WSL (`docker info` fails; no socket at `/var/run/docker.sock`, no `/mnt/wsl/**/docker.sock`). Needs Docker Desktop started with WSL integration enabled for this distro. |
| Delete `railpack.json` + `Procfile` | pending the local build |
| Clear service `builder` / `startCommand` overrides | pending |
| `healthcheckPath=/health` | pending |
| Deploy to a **staging** environment, verify `/health` 200 + smoke test | pending |
| Promote to production, keeping the last good deployment for rollback | pending |
| Enable autodeploy, confirm deployed SHA = HEAD | pending (GitHub App is now connected) |

**Standing rule as of 2026-10-01: no production service-config change without
proving it in a separate Railway environment first.**

## Dockerfile notes

- `python:3.11-slim`, matching `runtime.txt` / `.python-version`.
- `git` is installed to match the previous runtime's package set
  (`RAILPACK_DEPLOY_APT_PACKAGES=git`). No runtime shell-out to git was found
  in this codebase; remove it deliberately, in its own change, rather than as
  a side effect of the builder migration.
- `CMD` uses `python -m uvicorn`, not bare `uvicorn`: it does not depend on
  the console script being on PATH, which is exactly what broke the Railpack
  attempt. Shell form so `${PORT}` expands, with an `8000` default so
  `docker run -p 8000:8000` works locally with no env.
- **`supabase/migrations/` must stay in the image.** `api/main.py`'s lifespan
  runs `run_pending_migrations()` against those files before the app serves a
  single request. `.dockerignore` excludes `frontend/` (786MB), `venv/` and
  the git history, and nothing else that is imported or read at runtime — the
  whole repo minus those is about 7MB, so when in doubt, leave it in.
- Runs as an unprivileged user.

## The incident this replaces (2026-10-01)

Before: `railway.json` said `builder: NIXPACKS` + `uvicorn api.main:app …`;
`railpack.json` said `provider: python` + `uvicorn …`; `Procfile` said
`uvicorn …`; and the live service said `builder: RAILPACK` +
`python3 -m uvicorn …`.

| Attempt | Change | Result |
|---|---|---|
| 1 | Clear the service `startCommand` so a file would win | Build FAILED at `BUILD_IMAGE`: `⚠ Script start.sh not found`. An empty service start command does **not** fall back to any file. No outage. |
| 2 | Service `startCommand` = `uvicorn api.main:app …` | Build SUCCESS, container crash-loop: `uvicorn: command not found`. **502 outage.** |
| 3 | Service `startCommand` = `python3 -m uvicorn …` | Still crash-looping: `python3: command not found`. |
| 4 | `deploymentRollback` to the last good deployment | Health 200 restored. |
| 5 | Service `builder` = NIXPACKS | Build FAILED: *"Nixpacks was unable to generate a build plan for this app."* No outage. |
| 6 | Restored the service exactly as found | Healthy. |

### What actually went wrong

`get-service-config` reports `builder: RAILPACK`, and I read that as the
effective builder. It is the service-level **default**. The last known-good
deployment was built by **Nixpacks** from a CLI `railway up` snapshot — its
metadata has `reason: "deploy"` and **no `commitHash`**, and its build log
shows the Nixpacks shape (`[stage-0 6/8] RUN … python -m venv`). GitHub-sourced
builds use the service builder (Railpack), where neither `python3` nor
`uvicorn` is on PATH.

So the running service and the GitHub deploy path had silently diverged, and
had probably been diverging for some time. Trying to deploy from GitHub only
exposed it.

### Three lessons worth keeping

1. **A deployment can report SUCCESS while the container crash-loops.** Build
   status is not health. Poll `/health` until 200 and read the DEPLOY logs,
   not only the build logs. `healthcheckPath` exists so Railway enforces this
   itself — set it.
2. **`deploymentRedeploy` reuses the old config snapshot.** After a
   service-level change it silently keeps running the previous command and
   makes the fix look ineffective. Use `serviceInstanceDeployV2`.
3. **Do not trust a config API field as the effective value.** Confirm against
   a successful deployment's build logs.

## Deploying by hand (while autodeploy is off)

```bash
railway api 'mutation($svc:String!,$env:String!,$sha:String){
  serviceInstanceDeployV2(serviceId:$svc, environmentId:$env, commitSha:$sha)
}' --variables '{"svc":"<serviceId>","env":"<environmentId>","sha":"<commit>"}'
```

The commit must already be pushed — deploying a local-only SHA returns
`INTERNAL_SERVER_ERROR`.
