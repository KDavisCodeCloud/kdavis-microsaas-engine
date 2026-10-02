# mse-api build + deploy configuration

_Last updated 2026-10-01. The Dockerfile is live in production, verified end
to end through a staging environment._

## Decision

**The Dockerfile is the single source of truth for how this service starts.**
`CMD` carries the start command; `railpack.json` and `Procfile` are deleted;
`railway.json` keeps only `healthcheckPath` and the restart policy.

Reason: the repo previously had four config sources naming three different
start commands, and the *effective* builder was not the one any file claimed.
Reconciling them caused a ten-minute 502.

## Working configuration

```
rootDirectory   = kdavis-microsaas-engine   <- the build CONTEXT
dockerfilePath  = Dockerfile                <- relative to rootDirectory
startCommand    = ""                        <- the image's CMD governs
healthcheckPath = /health                   <- a failing deploy never takes traffic
```

### THE load-bearing fact

**MSE's code lives in a `kdavis-microsaas-engine/` SUBDIRECTORY of the repo.**
The repo root holds only `.github`, `01-governance-aws`, `LICENSE` and
`README.md`. Four failed attempts traced back to this one fact.

| # | Config tried | Result |
|---|---|---|
| 1 | `startCommand=""`, builder RAILPACK | Railpack ran anyway: a root Dockerfile does **not** override an explicitly-set builder. `⚠ Script start.sh not found`. |
| 2 | + `dockerfilePath=Dockerfile` | `couldn't locate the dockerfile at path Dockerfile in code archive`. |
| 3 | removed `Dockerfile` from `.dockerignore` | Same error, so that was not the cause -- but Railway DOES apply `.dockerignore` when packing the code archive, so the exclusion was still wrong to have. |
| 4 | `dockerfilePath=kdavis-microsaas-engine/Dockerfile` | Dockerfile finally **used**, then failed at `COPY requirements.txt` -- the build CONTEXT was still the repo root. |
| 5 | `rootDirectory=kdavis-microsaas-engine` + `dockerfilePath=Dockerfile` | green. |

Quirks worth knowing:

- There is **no `DOCKERFILE` value** in Railway's `Builder` enum. `builder`
  still reads `RAILPACK` and is simply inert once `dockerfilePath` is set.
- `builder: null` and `startCommand: null` are **silently ignored**. Use `""`
  to clear a start command.

## Dockerfile notes

- `python:3.11-slim`, matching `runtime.txt` / `.python-version`.
- `git` is installed to match the previous runtime's package set
  (`RAILPACK_DEPLOY_APT_PACKAGES=git`). No runtime shell-out to git was found;
  remove it deliberately, in its own change, not as a side effect.
- `CMD` uses `python -m uvicorn`, not bare `uvicorn`: it does not depend on the
  console script being on PATH, which is what broke the Railpack attempt.
  Shell form so `${PORT}` expands, with an `8000` default for local runs.
- **`supabase/migrations/` must stay in the image.** `api/main.py`'s lifespan
  runs `run_pending_migrations()` against those files before the app serves a
  request.
- Runs as an unprivileged user.

## Staging

`staging` (`bb49ff5e-f97e-4773-9a9f-392c192f4a17`), cloned from production,
at `mse-api-staging-abc7.up.railway.app`, same build config as production.

**It shares production's `DATABASE_URL`** because it was cloned. Migrations are
idempotent so a staging boot is a no-op against them, but keep staging smoke
tests READ-ONLY until staging gets its own database.

Smoke test that gated the production promotion:

```
/health      200  {"status":"ok","commit_sha":"<sha>"}
/docs        200
/openapi.json 200  (73 routes, incl. the 3 buyer-research endpoints)
/marketing/buyer-research 401  (auth enforced)
```

`/health` returning `commit_sha` is what makes "deployed SHA == HEAD"
verifiable from outside the platform.

## Autodeploy -- still blocked

Refused as of 2026-10-01 even after GitHub was reported connected. Both paths,
verbatim:

```
serviceInstanceAutoDeployUpdate:
  No workspace member has their GitHub account connected with access to
  this repository.

deploymentTriggerCreate:
  Cannot create deployment trigger for KDavisCodeCloud/kdavis-microsaas-engine
  because no one in the project has access to it
```

Caveat on reading those: `query { githubRepos }` returns **"Not Authorized"**
for the CLI token, so this token cannot see GitHub identities at all. The
refusal may be a limitation of the CLI auth context rather than proof the
GitHub App lacks the repo. The Railway dashboard runs as the GitHub-linked
browser session, so toggling autodeploy there is the fastest way to tell.

Until then, deploy explicitly:

```bash
railway api 'mutation($svc:String!,$env:String!,$sha:String){
  serviceInstanceDeployV2(serviceId:$svc, environmentId:$env, commitSha:$sha)
}' --variables '{"svc":"<serviceId>","env":"<environmentId>","sha":"<commit>"}'
```

The commit must already be pushed -- a local-only SHA returns
`INTERNAL_SERVER_ERROR`.

## Three lessons that cost an outage

1. **A deployment can report SUCCESS while the container crash-loops.** Build
   status is not health. Poll `/health` until 200 and read the DEPLOY logs.
   `healthcheckPath` now makes Railway enforce this.
2. **`deploymentRedeploy` reuses the old config snapshot**, so after a
   service-level change it keeps running the previous command and makes the fix
   look ineffective. Use `serviceInstanceDeployV2`.
3. **Do not trust a config-API field as the effective value.** Confirm against
   a successful deployment's build logs.

Rollback target kept for this migration: `e8352db3-85e6-4d36-b511-428462cad2f2`
(the last pre-Dockerfile deployment).
