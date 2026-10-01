# mse-api build + start configuration

_Last verified against production: 2026-10-01, after causing an outage._

## The short version

```jsonc
// railway.json -- DO NOT change the builder
{ "build":  { "builder": "NIXPACKS" },
  "deploy": { "startCommand": "python3 -m uvicorn api.main:app --host 0.0.0.0 --port $PORT" } }
```

Three rules, each learned by breaking it:

1. **The builder is NIXPACKS.** `railway.json` overrides the service-level
   default, and the Nixpacks image is the one where `python3` exists.
2. **The start command is `python3 -m uvicorn ...`**, not bare `uvicorn`.
3. **The service-level `startCommand` must be SET** — clearing it does not
   fall back to `railway.json`.

## The trap that caused the outage

`get-service-config` reports:

```json
"build": { "builder": "RAILPACK" }
```

**That is the service-level DEFAULT, not the effective builder.**
`railway.json` said `NIXPACKS` and `railway.json` wins. Seeing the mismatch,
I "corrected" the file to `RAILPACK` to match what the API reported — which
silently flipped the real builder from Nixpacks to Railpack.

The two images are not interchangeable:

| | Nixpacks (correct) | Railpack (what I switched to) |
|---|---|---|
| Build log signature | `[stage-0 6/8] RUN ... python -m venv` | `railpack-plan.json`, `railpack-builder:mise` |
| `python3` on PATH | yes | **no** |
| `uvicorn` on PATH | yes (venv) | **no** |

So under Railpack both spellings of the start command fail
(`python3: command not found` / `uvicorn: command not found`), and the
service 502s.

**Before changing `build.builder`, confirm which builder is actually
producing the running image by reading a successful deployment's BUILD
logs.** The config API will not tell you.

## The full sequence, for the record

| Attempt | Change | Result |
|---|---|---|
| 1 | Cleared service `startCommand` so `railway.json` would win | Build FAILED at `BUILD_IMAGE`: `⚠ Script start.sh not found`. No outage. |
| 2 | Service `startCommand` = `uvicorn api.main:app ...` (+ builder flipped to RAILPACK) | Build SUCCESS, container crash-loop: `uvicorn: command not found`. **502.** |
| 3 | Service `startCommand` = `python3 -m uvicorn ...` (builder still RAILPACK) | Still crash-looping: `python3: command not found`. **502.** |
| 4 | `deploymentRollback` to the last known-good deployment | Health 200 restored. |
| 5 | Reverted `railway.json` builder to `NIXPACKS` | Healthy. |

Two things worth internalising:

- **A deployment can report SUCCESS while the process never starts.** Build
  status is not health. Always poll `/health` until 200 after any build or
  start-command change, and read the DEPLOY logs, not just the build ones.
- **`deploymentRedeploy` reuses the old config snapshot.** After changing a
  service-level setting, trigger a FRESH deploy (`serviceInstanceDeployV2`);
  a redeploy will keep running the previous command and look like the change
  had no effect.

## To change the start command

1. Edit `railway.json`.
2. Match the `Procfile`.
3. Push the same string to the service-level `startCommand`.
4. Deploy fresh, then poll `/health` until 200.

## Autodeploy

Autodeploy on push is **off** for `mse-api` (`repoTriggers: []`). Both
`deploymentTriggerCreate` and `serviceInstanceAutoDeployUpdate` refuse it:

```
No workspace member has their GitHub account connected with access to
this repository.
```

That needs a human: connect the GitHub account (or install the Railway
GitHub App on `KDavisCodeCloud/kdavis-microsaas-engine`) from the Railway
dashboard. Until then deploy explicitly with `serviceInstanceDeployV2`,
passing a commit that is already pushed — a local-only SHA returns
`INTERNAL_SERVER_ERROR`.
