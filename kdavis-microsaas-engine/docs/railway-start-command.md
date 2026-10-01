# mse-api start command — why it is set in three places

_Last verified against production: 2026-10-01, the hard way._

## The short version

The canonical command is:

```
python3 -m uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

It must be set **on the Railway service**, and `railway.json` + `Procfile`
must carry the same string. Two things that look reasonable and are not:

- **Do not clear the service-level value** expecting `railway.json` to take
  over. It does not; the build fails.
- **Do not use bare `uvicorn`.** It is not on PATH in this image.

## What we actually tested

Before 2026-10-01 the three sources disagreed:

| Where | Value |
|---|---|
| `railway.json` `deploy.startCommand` | `uvicorn api.main:app ...` |
| `Procfile` | `uvicorn api.main:app ...` |
| **Railway service override (live)** | **`python3 -m uvicorn api.main:app ...`** |
| `railway.json` `build.builder` | `NIXPACKS` |
| **Railway service builder (live)** | **`RAILPACK`** |

The service-level values win, so the repo described something that did not
run. Reconciling them took three attempts.

**Attempt 1 — clear the service override so `railway.json` wins.** The
mutation succeeded; the next deploy FAILED at `BUILD_IMAGE`:

```
Railpack 0.40.1
  ⚠ Script start.sh not found
error | railpack prepare exited with an error
```

An empty service-level start command does **not** fall back to
`railway.json`. Railpack went looking for a `start.sh`, did not find one,
and failed during prepare. No outage — the previous deployment kept serving.

**Attempt 2 — set the service override to `uvicorn api.main:app ...`,
matching what `railway.json` and the `Procfile` said.** The BUILD succeeded
and Railway reported the deployment SUCCESS, but the container crash-looped:

```
/bin/bash: line 1: uvicorn: command not found
```

**This caused a real outage.** `/health` served 502 for several minutes
until the command was rolled back. Note the trap: a deployment can be
SUCCESS (the image built) while the process never starts. Build status is
not health — always check `/health` after a start-command change.

So in this Railpack image `python3` IS on PATH and bare `uvicorn` is NOT.
The earlier Procfile incident (`python3: command not found`) was under a
different builder, and generalising from it was the mistake.

**Attempt 3 — `python3 -m uvicorn ...` everywhere.** Healthy.

## The rule

`railway.json` records the decision and is reviewable in git; the
service-level value is how Railway actually receives it. To change it:

1. Edit `railway.json`.
2. Match the `Procfile`.
3. Push the same string to the service-level `startCommand`.
4. Deploy, then **poll `/health` until it returns 200** — not just the
   deployment status.

## Autodeploy

Autodeploy on push is **off** for `mse-api` (`repoTriggers: []`). Both
`deploymentTriggerCreate` and `serviceInstanceAutoDeployUpdate` refuse it:

```
No workspace member has their GitHub account connected with access to
this repository.
```

That needs a human: connect the GitHub account (or install the Railway
GitHub App on `KDavisCodeCloud/kdavis-microsaas-engine`) from the Railway
dashboard. Until then, deploy explicitly:

```bash
railway api 'mutation($svc:String!,$env:String!,$sha:String){
  serviceInstanceDeployV2(serviceId:$svc, environmentId:$env, commitSha:$sha)
}' --variables '{"svc":"<serviceId>","env":"<environmentId>","sha":"<commit>"}'
```

The commit must already be pushed to GitHub — deploying a local-only SHA
returns `INTERNAL_SERVER_ERROR`.
