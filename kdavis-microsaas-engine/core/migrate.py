"""
Migration runner — applies supabase/migrations/*.sql to the live database
on every app startup (api/main.py's lifespan, before the app starts
serving traffic).

Built 2026-09-16 closing the other half of kdavis-agentic-platform's
GAPS.md #15 ("no migration runner anywhere in the deploy pipeline").
That repo's own db/migrate.py was already built for Cloud Decoded's
schema on 2026-09-15 -- this repo had no equivalent at all. Concretely
hit twice in the same session: 20260914221640_icp_selling_stage.sql and
20260916000045_consulting_infra_icp.sql both sat committed and deployed
for a day-plus before being applied to production by hand, and a third
migration (20260916000046_cloud_decoded_icp.sql) was written the same
way this fix was being planned. This exists so that never has to happen
again.

Design (deliberately mirrors db/migrate.py's proven pattern, adapted for
two real differences from that repo):

- **Separate tracking table.** kdavis-agentic-platform and this repo
  share ONE physical Supabase Postgres database (gjezchcoyytxcpsbvkrg,
  confirmed repeatedly this session) -- that repo's schema_migrations
  table already exists there with 39 rows tracking ITS migrations
  (db/migrations/, 3-digit-prefixed filenames). Using the same table
  name here would mix two unrelated migration histories into one table.
  This repo's own tracking table is `mse_schema_migrations`.
- **Separate advisory lock ID.** Same shared-database reasoning --
  reusing db/migrate.py's lock constant (847_291_055) would make this
  app's boot needlessly contend with Cloud Decoded's on every deploy
  where both happen to restart around the same time. `_ADVISORY_LOCK_ID`
  below is a different arbitrary constant.
- **No ambient connection pool to reuse.** Unlike Cloud Decoded's
  api/main.py (which already creates one asyncpg.Pool for the whole
  app's lifetime), this app has never used asyncpg directly -- every
  other module here goes through core/supabase_client.py's REST-based
  client, which has no raw-SQL execution capability at all. Rather than
  introduce a long-lived pool the rest of the app doesn't need, this
  module opens one asyncpg connection, runs the pending batch, and
  closes it -- self-contained, no new ambient DB-access pattern for
  api/main.py's lifespan to manage.
- **Rollback scripts are never auto-discovered.** This directory
  contains at least one manual, opt-in rollback file
  (20260831000035_dist_phase8_mse_leads_alter_ROLLBACK.sql) sitting
  alongside its forward migration with the same numeric prefix. Blindly
  including it in a numeric-order auto-apply would mean the runner could
  execute a DROP COLUMN rollback as if it were the next pending forward
  migration. `_discover_migrations()` explicitly excludes any filename
  containing "ROLLBACK" (case-insensitive) and logs each one it skips,
  so a future rollback file added the same way is safe by default, not
  by luck.
- **Deterministic ordering on a same-prefix collision.** This
  directory's naming convention (YYYYMMDDHHMMSS_description.sql) mostly
  guarantees a unique, chronological numeric prefix, but three real
  existing files share the exact prefix 20260830000030 (parallel work
  merged without renumbering). Sorting by prefix alone leaves their
  relative order dependent on filesystem glob() return order, which is
  not guaranteed consistent across environments. Sort key is
  `(prefix, filename)` -- same prefix breaks the tie alphabetically by
  full filename, deterministic everywhere this ever runs.
- Everything else -- the tracking table's own creation, reading what's
  already applied, running every pending file, and recording it -- is
  one all-or-nothing transaction guarded by `pg_advisory_xact_lock`,
  exactly matching db/migrate.py's own reasoning about Supabase's
  transaction-mode pooler (a session-scoped lock is not guaranteed to
  hold across one underlying server connection under that pooling mode;
  a transaction-scoped lock is, and auto-releases on commit/rollback
  with no manual unlock to leak on a crash).
- Fails closed: if a migration's SQL raises, this raises too, and
  api/main.py's lifespan does not complete -- the app does not start
  against a schema application code doesn't match. Every migration
  written from this point forward must follow this repo's own existing
  convention (IF NOT EXISTS / ON CONFLICT DO UPDATE, same as every
  migration this session's audit found already uses) so a retried batch
  after a transient failure is safe to reapply from the top -- migration
  20260909000044's non-idempotent CREATE POLICY (no DROP POLICY IF
  EXISTS first) is exactly the kind of thing to never repeat once this
  runner is live, since anything applied before this runner shipped is
  bootstrap-seeded as already-applied and will never be re-run, but
  anything written after this must be safe to retry.

Bootstrap note: every migration file that existed in this repo as of
2026-09-16 (49 forward files, the one ROLLBACK excluded) was inserted
directly into a freshly-created mse_schema_migrations table BEFORE this
runner's first deploy -- see knowledge/sops/devops/2026-09-16-mse-migration-runner.md
in kdavis-agentic-platform for the exact one-time script and its output.
Skipping that step would have made this runner's first boot attempt to
apply all 49 files from scratch, including #44's non-idempotent policy
creation above -- a guaranteed crash on the very deploy meant to fix
this class of problem.
"""

import logging
import re
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent.parent / "supabase" / "migrations"

# Arbitrary, unique to this app's migration lock -- deliberately different
# from kdavis-agentic-platform's db/migrate.py constant (847_291_055) even
# though both apps share one physical database. See module docstring.
_ADVISORY_LOCK_ID = 918_442_113

_FILENAME_RE = re.compile(r"^(\d+)_.*\.sql$")
_ROLLBACK_RE = re.compile(r"rollback", re.IGNORECASE)


def _discover_migrations() -> list[Path]:
    """Every supabase/migrations/*.sql file except manual rollback scripts,
    sorted by (numeric prefix, filename) -- the filename tiebreaker makes
    ordering deterministic even for the real same-prefix collision this
    directory already has (three 20260830000030_*.sql files)."""
    numbered: list[tuple[int, str, Path]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if _ROLLBACK_RE.search(path.name):
            log.info("[Migrate] Skipping rollback script %s (never auto-applied)", path.name)
            continue
        match = _FILENAME_RE.match(path.name)
        if not match:
            raise ValueError(
                f"Migration file {path.name} doesn't match the required "
                f"<digits>_description.sql naming convention -- refusing to guess its order"
            )
        numbered.append((int(match.group(1)), path.name, path))
    numbered.sort(key=lambda triple: (triple[0], triple[1]))
    return [path for _, _, path in numbered]


async def run_pending_migrations(database_url: str) -> list[str]:
    """
    Applies every supabase/migrations/*.sql file not yet recorded in
    mse_schema_migrations, in order, as one all-or-nothing transaction.
    Returns the list of filenames applied this call (empty on a normal
    boot once the schema is caught up).

    Opens and closes its own connection -- this app has no ambient
    asyncpg pool for this to reuse (see module docstring).

    Raises on the first migration that fails, rolling back the entire
    batch -- callers (api/main.py's lifespan) must let this propagate;
    do not catch-and-continue, that is exactly the silent-drift failure
    mode this module exists to prevent.
    """
    # statement_cache_size=0: DATABASE_URL points at Supabase's
    # transaction-mode pooler, which does not support asyncpg's default
    # prepared-statement caching across requests on the same apparent
    # connection -- confirmed this exact session (DuplicatePreparedStatementError
    # on a plain asyncpg.connect() against this same database).
    conn = await asyncpg.connect(database_url, statement_cache_size=0)
    try:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _ADVISORY_LOCK_ID)

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mse_schema_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            applied_rows = await conn.fetch("SELECT filename FROM mse_schema_migrations")
            already_applied = {r["filename"] for r in applied_rows}

            newly_applied: list[str] = []
            for path in _discover_migrations():
                if path.name in already_applied:
                    continue

                sql = path.read_text()
                log.info("[Migrate] Applying %s ...", path.name)
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO mse_schema_migrations (filename) VALUES ($1)",
                    path.name,
                )
                newly_applied.append(path.name)

            if newly_applied:
                log.info(
                    "[Migrate] %d migration(s) applied: %s",
                    len(newly_applied), ", ".join(newly_applied),
                )
            else:
                log.info("[Migrate] Schema up to date — 0 pending migrations")

            return newly_applied
    finally:
        await conn.close()
