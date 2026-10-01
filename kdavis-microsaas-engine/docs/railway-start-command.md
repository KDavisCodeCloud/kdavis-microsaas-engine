# mse-api deploy configuration — BLOCKED, read before touching

_Investigated 2026-10-01. One outage caused. Service left exactly as found._

## Status

**Do not change the build or start configuration without a plan for all
three config sources below.** `mse-api` currently runs healthy on a
deployment that was created by a CLI `railway up`, NOT from GitHub. Every
GitHub-sourced deploy attempted on 2026-10-01 failed.

As-found (and restored) service settings:

```
builder      = RAILPACK
startCommand = python3 -m uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

## Why it is blocked: three competing config sources

| Source | Says |
|---|---|
| `railway.json` | `builder: NIXPACKS`, `startCommand: uvicorn api.main:app ...` |
| `railpack.json` | `provider: python`, `startCommand: uvicorn api.main:app ...` |
| `Procfile` | `web: uvicorn api.main:app ...` |
| **Railway service (effective)** | **`builder: RAILPACK`, `startCommand: python3 -m uvicorn ...`** |

The **service-level settings win** for GitHub-sourced builds;
`railway.json`'s builder is ignored. Confirmed by experiment: setting the
service builder to NIXPACKS did change the build to Nixpacks, while editing
`railway.json` did not.

## What was tried, and what each attempt proved

| # | Change | Result |
|---|---|---|
| 1 | Clear service `startCommand` so a file would win | Build FAILED at `BUILD_IMAGE`: `⚠ Script start.sh not found`. An empty service start command does not fall back to any file. No outage. |
| 2 | Service `startCommand` = `uvicorn api.main:app ...` | Build SUCCESS, container crash-loop: `uvicorn: command not found`. **502 outage.** |
| 3 | Service `startCommand` = `python3 -m uvicorn ...` (builder RAILPACK) | Crash-loop: `python3: command not found`. **Still 502.** Neither binary is on PATH in the Railpack image. |
| 4 | `deploymentRollback` to the last good deployment | Health 200 restored. |
| 5 | Service `builder` = NIXPACKS (to match the good image) | Build FAILED: *"Nixpacks was unable to generate a build plan for this app."* No outage — the old deployment kept serving. |

## The core finding

The **last known-good deployment was built by Nixpacks**, and its build log
shows the classic Nixpacks shape:

```
[stage-0 6/8] RUN ... python -m venv ...
Successfully installed PyJWT-2.9.0 anthropic-0.116.0 asyncpg-0.29.0 ...
```

Its metadata has `reason: "deploy"` and **no `commitHash`** — i.e. it came
from a CLI `railway up` snapshot, not from GitHub. GitHub-sourced builds use
the service builder (RAILPACK), and under Railpack the start command cannot
find `python3` or `uvicorn`.

So the running service and the GitHub deploy path are not building the same
way, and they have probably diverged for some time — this is a pre-existing
condition that was only exposed by trying to deploy from GitHub.

`.railwayignore` already documents a related CLI-vs-GitHub divergence (an
untracked local file that breaks Railpack's config parser), which is more
evidence that CLI deploys have been the working path here.

## What needs deciding (not safe to guess)

One of:

1. **Commit to Railpack** and find the start command that works in that
   image — likely the venv interpreter by absolute path, e.g.
   `/app/.venv/bin/python -m uvicorn ...`. Verify by `railway ssh` into a
   running Railpack container and checking `which python python3 uvicorn`
   before deploying.
2. **Commit to Nixpacks** and fix why it cannot generate a plan for this
   repo from a GitHub checkout even though `requirements.txt`,
   `runtime.txt` and `.python-version` are all tracked. Possibly
   `railpack.json`'s presence, possibly something else — the build log
   truncates before listing what it saw.
3. **Keep deploying via CLI `railway up`** from a native Linux path (not a
   WSL mount — see the five-file corruption history) and accept that
   GitHub-sourced deploys do not work.

## Two general lessons from the outage

- **A deployment can report SUCCESS while the process never starts.** Build
  status is not health. Always poll `/health` until 200 and read the DEPLOY
  logs, not only the build logs.
- **`deploymentRedeploy` reuses the old config snapshot**, so after changing
  a service-level setting it silently keeps running the previous command and
  makes the fix look ineffective. Use `serviceInstanceDeployV2` for a fresh
  build.
- **Do not trust `get-service-config`'s `builder` field as the effective
  builder** without checking a successful deployment's build logs.

## Autodeploy

Also blocked, separately. `repoTriggers` is empty and both
`deploymentTriggerCreate` and `serviceInstanceAutoDeployUpdate` refuse:

```
No workspace member has their GitHub account connected with access to
this repository.
```

Needs a human to connect the GitHub account / install the Railway GitHub App
on `KDavisCodeCloud/kdavis-microsaas-engine` from the Railway dashboard.
