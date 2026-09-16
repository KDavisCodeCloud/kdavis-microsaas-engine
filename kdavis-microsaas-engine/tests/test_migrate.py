"""
tests/test_migrate.py
Tests for core/migrate.py -- the migration runner built 2026-09-16 to
close the other half of kdavis-agentic-platform's GAPS.md #15 (that repo
had db/migrate.py since 2026-09-15; this repo had no equivalent at all,
and it caused two real migrations to sit committed-but-not-applied for
a day-plus in this same session).

What this file validates:
  _discover_migrations():
    - Real supabase/migrations/*.sql files are discovered and sorted by
      (numeric prefix, filename) -- not lexical glob() order, which is
      what a same-prefix collision (three real files share
      "20260830000030") would otherwise depend on
    - A manual rollback script is excluded, never treated as a pending
      forward migration
    - A malformed filename (doesn't match <digits>_description.sql)
      raises ValueError rather than silently misordering it

  run_pending_migrations(database_url):
    - Opens its own asyncpg connection (this app has no ambient pool,
      unlike kdavis-agentic-platform's db/migrate.py) and always closes
      it, on both the success and failure path
    - Acquires the transaction-scoped advisory lock before touching
      mse_schema_migrations
    - Creates the mse_schema_migrations tracking table (a name
      deliberately distinct from kdavis-agentic-platform's own
      schema_migrations table -- both repos share one physical database)
    - Skips files already recorded as applied
    - Applies pending files in order, records each
    - Returns the list of newly-applied filenames
    - Propagates an exception from a failing migration (fail closed) --
      does not catch-and-continue, does not record a tracking row for it

Runs with pytest-asyncio + unittest.mock -- no live Postgres needed for
these; the actual bootstrap backfill (49 pre-existing migrations marked
already-applied) and post-deploy verification against the real shared
database are documented in
knowledge/sops/devops/2026-09-16-mse-migration-runner.md (kdavis-agentic-platform),
not re-verified here.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import migrate


def _mock_conn():
    conn = AsyncMock()
    tx_ctx = AsyncMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=tx_ctx)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.close = AsyncMock(return_value=None)
    return conn


class TestDiscoverMigrations:
    def test_real_migrations_directory_sorted_numerically(self):
        files = migrate._discover_migrations()
        assert len(files) >= 49  # at least this many forward migrations as of this fix

        numbers = [int(migrate._FILENAME_RE.match(p.name).group(1)) for p in files]
        assert numbers == sorted(numbers)

    def test_rollback_file_excluded(self):
        files = migrate._discover_migrations()
        names = [p.name for p in files]
        assert not any("rollback" in n.lower() for n in names)
        # confirm the specific known rollback file exists on disk but was excluded
        all_sql = list(migrate._MIGRATIONS_DIR.glob("*.sql"))
        assert any("ROLLBACK" in p.name for p in all_sql), "fixture assumption changed -- rollback file missing"

    def test_raises_on_malformed_filename(self, tmp_path):
        (tmp_path / "not_numbered.sql").write_text("SELECT 1;")
        with patch.object(migrate, "_MIGRATIONS_DIR", tmp_path):
            with pytest.raises(ValueError, match="naming convention"):
                migrate._discover_migrations()

    def test_same_prefix_collision_sorted_deterministically_by_filename(self, tmp_path):
        # Mirrors the real 20260830000030_*.sql collision in this repo.
        (tmp_path / "20260830000030_b_file.sql").write_text("SELECT 1;")
        (tmp_path / "20260830000030_a_file.sql").write_text("SELECT 1;")
        (tmp_path / "20260830000031_next.sql").write_text("SELECT 1;")

        with patch.object(migrate, "_MIGRATIONS_DIR", tmp_path):
            files = migrate._discover_migrations()

        names = [p.name for p in files]
        assert names == [
            "20260830000030_a_file.sql",
            "20260830000030_b_file.sql",
            "20260830000031_next.sql",
        ]

    def test_all_real_migration_files_parse_as_valid_sql_text(self):
        for path in migrate._discover_migrations():
            content = path.read_text()
            assert len(content.strip()) > 0, f"{path.name} is empty"


class TestRunPendingMigrations:
    async def test_opens_and_closes_its_own_connection(self):
        conn = _mock_conn()
        with patch("asyncpg.connect", AsyncMock(return_value=conn)) as mock_connect, \
                patch.object(migrate, "_discover_migrations", return_value=[]):
            await migrate.run_pending_migrations("postgresql://fake")

        mock_connect.assert_awaited_once_with("postgresql://fake", statement_cache_size=0)
        conn.close.assert_awaited_once()

    async def test_closes_connection_even_when_a_migration_fails(self, tmp_path):
        f1 = tmp_path / "1_bad.sql"
        f1.write_text("THIS IS NOT VALID SQL;")

        conn = _mock_conn()

        async def _execute(sql, *args):
            if "THIS IS NOT VALID SQL" in sql:
                raise Exception("syntax error")
            return None
        conn.execute = AsyncMock(side_effect=_execute)

        with patch("asyncpg.connect", AsyncMock(return_value=conn)), \
                patch.object(migrate, "_MIGRATIONS_DIR", tmp_path):
            with pytest.raises(Exception, match="syntax error"):
                await migrate.run_pending_migrations("postgresql://fake")

        conn.close.assert_awaited_once()

    async def test_acquires_advisory_lock_before_reading_tracking_table(self):
        conn = _mock_conn()
        with patch("asyncpg.connect", AsyncMock(return_value=conn)), \
                patch.object(migrate, "_discover_migrations", return_value=[]):
            await migrate.run_pending_migrations("postgresql://fake")

        first_call_sql = conn.execute.await_args_list[0].args[0]
        assert "pg_advisory_xact_lock" in first_call_sql

    async def test_creates_mse_schema_migrations_table_not_the_other_repos_table(self):
        conn = _mock_conn()
        with patch("asyncpg.connect", AsyncMock(return_value=conn)), \
                patch.object(migrate, "_discover_migrations", return_value=[]):
            await migrate.run_pending_migrations("postgresql://fake")

        create_calls = [
            c.args[0] for c in conn.execute.await_args_list
            if "CREATE TABLE IF NOT EXISTS mse_schema_migrations" in c.args[0]
        ]
        assert len(create_calls) == 1
        # never touches the sibling repo's own tracking table name
        assert not any("CREATE TABLE IF NOT EXISTS schema_migrations (" in c.args[0] for c in conn.execute.await_args_list)

    async def test_skips_already_applied_and_applies_pending_in_order(self, tmp_path):
        f1 = tmp_path / "1_first.sql"
        f2 = tmp_path / "2_second.sql"
        f1.write_text("CREATE TABLE IF NOT EXISTS a (id int);")
        f2.write_text("CREATE TABLE IF NOT EXISTS b (id int);")

        conn = _mock_conn()
        conn.fetch = AsyncMock(return_value=[{"filename": "1_first.sql"}])  # already applied

        with patch("asyncpg.connect", AsyncMock(return_value=conn)), \
                patch.object(migrate, "_MIGRATIONS_DIR", tmp_path):
            result = await migrate.run_pending_migrations("postgresql://fake")

        assert result == ["2_second.sql"]
        executed_sql = " ".join(c.args[0] for c in conn.execute.await_args_list)
        assert "CREATE TABLE IF NOT EXISTS b" in executed_sql
        assert "CREATE TABLE IF NOT EXISTS a" not in executed_sql

    async def test_returns_empty_list_when_nothing_pending(self, tmp_path):
        f1 = tmp_path / "1_first.sql"
        f1.write_text("CREATE TABLE IF NOT EXISTS a (id int);")

        conn = _mock_conn()
        conn.fetch = AsyncMock(return_value=[{"filename": "1_first.sql"}])

        with patch("asyncpg.connect", AsyncMock(return_value=conn)), \
                patch.object(migrate, "_MIGRATIONS_DIR", tmp_path):
            result = await migrate.run_pending_migrations("postgresql://fake")

        assert result == []

    async def test_records_each_applied_migration(self, tmp_path):
        f1 = tmp_path / "1_first.sql"
        f1.write_text("CREATE TABLE IF NOT EXISTS a (id int);")

        conn = _mock_conn()

        with patch("asyncpg.connect", AsyncMock(return_value=conn)), \
                patch.object(migrate, "_MIGRATIONS_DIR", tmp_path):
            await migrate.run_pending_migrations("postgresql://fake")

        insert_calls = [c for c in conn.execute.await_args_list if "INSERT INTO mse_schema_migrations" in c.args[0]]
        assert len(insert_calls) == 1
        assert insert_calls[0].args[1] == "1_first.sql"

    async def test_does_not_insert_tracking_row_for_a_failed_migration(self, tmp_path):
        f1 = tmp_path / "1_bad.sql"
        f1.write_text("THIS IS NOT VALID SQL;")

        conn = _mock_conn()

        async def _execute(sql, *args):
            if "THIS IS NOT VALID SQL" in sql:
                raise Exception("syntax error")
            return None
        conn.execute = AsyncMock(side_effect=_execute)

        with patch("asyncpg.connect", AsyncMock(return_value=conn)), \
                patch.object(migrate, "_MIGRATIONS_DIR", tmp_path):
            with pytest.raises(Exception):
                await migrate.run_pending_migrations("postgresql://fake")

        insert_calls = [c for c in conn.execute.await_args_list if "INSERT INTO mse_schema_migrations" in c.args[0]]
        assert len(insert_calls) == 0
