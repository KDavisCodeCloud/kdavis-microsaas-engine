# mse-api start command — why it is set in three places

_Last verified against production: 2026-10-01._

## The short version

The start command must be set **on the Railway service**, and
`railway.json` + `Procfile` must carry the same string. Do **not** clear the
service-level value expecting `railway.json` to take over — that fails the
build.

Canonical value, identical in all three places:

```
uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

## What we actually tested

Before 2026-10-01 the three sources disagreed:

| Where | Value |
|---|---|
| `railway.json` `deploy.startCommand` | `uvicorn api.main:app ...` |
| `Procfile` | `uvicorn api.main:app ...` |
| **Railway service override (live)** | **`python3 -m uvicorn api.main:app ...`** |
| `railway.json` `build.builder` | `NIXPACKS` |
| **Railway service builder (live)** | **`RAILPACK`** |

The service-level values win, so the repo was describing something that did
not run. Worse, `python3 -m uvicorn` is the exact form that crash-looped
earlier with `python3: command not found` when it came from the Procfile —
it happens to work under the current Railpack image, but it is the riskier
of the two spellings and it was live by accident, not by decision.

**Attempt 1 — clear the service override so `railway.json` wins.** Setting
`startCommand` to `""` via `serviceInstanceUpdate` succeeded, and the next
deploy FAILED at the `BUILD_IMAGE` stage:

```
Railpack 0.40.1
  ⚠ Script start.sh not found
error | railpack prepare exited with an error
```

So an empty service-level start command does **not** fall back to
`railway.json`. Railpack went looking for a `start.sh`, did not find one,
and failed during prepare. Production was unaffected — the previous
deployment kept serving `/health` 200 throughout.

**Attempt 2 — set the service override to `railway.json`'s exact value.**
Deploy SUCCESS, `/health` 200.

## The rule

`railway.json` is the source of truth in the sense that it records the
decision and is reviewable in git; the service-level value is how Railway
actually receives it. When you change the start command:

1. Edit `railway.json`.
2. Match the `Procfile` to it.
3. Push the service-level `startCommand` to the same string.

`build.builder` in `railway.json` is now `RAILPACK`, matching what the
service really uses, so the file no longer asserts a builder that is not in
play.

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
