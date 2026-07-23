"""Guard rails for the Book Navigator migrations (24/25).

These are string-level checks: they pin the properties that broke (or would
break) against upstream schema changes, without needing a live SurrealDB.
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "open_notebook" / "database" / "migrations"


def read(name: str) -> str:
    return (MIGRATIONS / name).read_text()


class TestMigration24:
    def test_files_exist(self):
        assert (MIGRATIONS / "24.surrealql").exists()
        assert (MIGRATIONS / "24_down.surrealql").exists()

    def test_speaker_profile_seeded_before_episode_profile(self):
        """episode_profile.speaker_config is a record link since upstream
        migration 20 — the linked speaker row must exist first."""
        sql = read("24.surrealql")
        speaker_pos = sql.index("insert into speaker_profile")
        episode_pos = sql.index("insert into episode_profile")
        assert speaker_pos < episode_pos

    def test_speaker_config_is_record_link_subquery(self):
        sql = read("24.surrealql")
        assert 'speaker_config: (SELECT VALUE id FROM ONLY speaker_profile' in sql
        # The pre-migration-20 string form must not come back.
        assert 'speaker_config: "book_navigator_mentor"' not in sql

    def test_episode_chapter_fields_defined(self):
        sql = read("24.surrealql")
        for field in ("audiobook ON TABLE episode", "chapter_index", "chapter_title"):
            assert field in sql


class TestMigration25:
    def test_files_exist(self):
        assert (MIGRATIONS / "25.surrealql").exists()
        assert (MIGRATIONS / "25_down.surrealql").exists()

    def test_book_figure_schema(self):
        sql = read("25.surrealql")
        assert "DEFINE TABLE IF NOT EXISTS book_figure SCHEMAFULL" in sql
        for field in ("source", "page", "chapter_index", "path", "kind", "caption"):
            assert f"DEFINE FIELD IF NOT EXISTS {field} ON" in sql, field

    def test_briefing_update_is_guarded_against_reapplication(self):
        """Migrations may re-run on restored DBs; the briefing append must be
        idempotent via the string::contains guard."""
        sql = read("25.surrealql")
        assert "ここで図が出てきます" in sql
        assert "!string::contains" in sql

    def test_down_drops_book_figure(self):
        sql = read("25_down.surrealql")
        assert "REMOVE TABLE" in sql and "book_figure" in sql


class TestRegistration:
    def test_migrations_24_and_25_are_registered(self):
        """AsyncMigrationManager hard-codes its migration list; a new file
        without registration silently never runs."""
        src = (
            MIGRATIONS.parent / "async_migrate.py"
        ).read_text()
        for name in ("24.surrealql", "24_down.surrealql", "25.surrealql", "25_down.surrealql"):
            assert f"migrations/{name}" in src, f"{name} not registered"

    def test_no_numbering_gap(self):
        """Every up migration from 1..N exists on disk (upstream renumber guard)."""
        numbers = sorted(
            int(p.stem)
            for p in MIGRATIONS.glob("*.surrealql")
            if p.stem.isdigit()
        )
        assert numbers == list(range(1, numbers[-1] + 1))


class TestMigration30:
    def test_files_exist_and_registered(self):
        assert (MIGRATIONS / "30.surrealql").exists()
        assert (MIGRATIONS / "30_down.surrealql").exists()
        src = (MIGRATIONS.parent / "async_migrate.py").read_text()
        for name in ("30.surrealql", "30_down.surrealql"):
            assert f"migrations/{name}" in src, f"{name} not registered"

    def test_quality_event_schema(self):
        sql = read("30.surrealql")
        assert "DEFINE TABLE IF NOT EXISTS quality_event" in sql
        for kind in ("transcript_gate", "ask_refusal", "mentor_low_evidence"):
            assert kind in sql
        assert "FLEXIBLE TYPE option<object>" in sql  # details evolves with prompts

    def test_episode_feedback_field(self):
        sql = read("30.surrealql")
        assert "feedback ON TABLE episode" in sql
        assert '"up", "down"' in sql

    def test_down_reverses_both(self):
        sql = read("30_down.surrealql")
        assert "REMOVE TABLE IF EXISTS quality_event" in sql
        assert "REMOVE FIELD IF EXISTS feedback ON TABLE episode" in sql


class TestMigration31:
    def test_files_exist_and_registered(self):
        assert (MIGRATIONS / "31.surrealql").exists()
        assert (MIGRATIONS / "31_down.surrealql").exists()
        src = (MIGRATIONS.parent / "async_migrate.py").read_text()
        for name in ("31.surrealql", "31_down.surrealql"):
            assert f"migrations/{name}" in src, f"{name} not registered"

    def test_seeds_consultant_as_editable_default(self):
        sql = read("31.surrealql")
        assert "INSERT IGNORE INTO mentor_profile" in sql  # 再適用で上書きしない
        assert "コンサルタント" in sql  # 既存挙動はシードとして維持
        assert "idx_mentor_profile_name" in sql and "UNIQUE" in sql
