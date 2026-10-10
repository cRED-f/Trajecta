"""SQLite database — Trajecta's own store (not Deep Agents memory).

Owns task metadata, trajectory metadata, skill registry, and config records.
Separate from `langgraph.db`, which LangGraph owns (checkpointer + long-term store).
Lives under `.trajecta/data/trajecta.db`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

# aiosqlite exists as a dependency; import lazily so a missing wheel breaks
# only the code path that needs it, not the whole package import.


class SQLiteDatabase:
    """Async SQLite wrapper with migrations and connection pool.

    Usage:
        db = SQLiteDatabase(path)
        await db.open()
        await db.execute(...)
        rows = await db.fetch(...)
        await db.close()
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: Any | None = None  # aiosqlite.Connection
        self._write_lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def open(self) -> None:
        import aiosqlite

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._migrate()

    async def _migrate(self) -> None:
        """Create schema and apply migrations."""

        assert self._conn is not None

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            )
            """
        )

        cursor = await self._conn.execute(
            """
            SELECT version
            FROM schema_version
            ORDER BY version DESC
            LIMIT 1
            """
        )

        row = await cursor.fetchone()

        version = row["version"] if row else 0

        if version < 1:
            await self._migrate_v1()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (1)"
            )

            version = 1

        if version < 2:
            await self._migrate_v2()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (2)"
            )

            version = 2

        if version < 3:
            await self._migrate_v3()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (3)"
            )

            version = 3

        if version < 4:
            await self._migrate_v4()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (4)"
            )

            version = 4

        if version < 5:
            await self._migrate_v5()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (5)"
            )

            version = 5

        if version < 6:
            await self._migrate_v6()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (6)"
            )

            version = 6

        if version < 7:
            await self._migrate_v7()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (7)"
            )

            version = 7

        if version < 8:
            await self._migrate_v8()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (8)"
            )

            version = 8

        if version < 9:
            await self._migrate_v9()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (9)"
            )

            version = 9

        if version < 10:
            await self._migrate_v10()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (10)"
            )

            version = 10

        if version < 11:
            await self._migrate_v11()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (11)"
            )

            version = 11

        if version < 12:
            await self._migrate_v12()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (12)"
            )

            version = 12

        if version < 13:
            await self._migrate_v13()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (13)"
            )

            version = 13

        if version < 14:
            await self._migrate_v14()

            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (14)"
            )

            version = 14

        if version < 15:
            await self._migrate_v15()
            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (15)"
            )
            version = 15

        if version < 16:
            await self._migrate_v16()
            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (16)"
            )
            version = 16

        if version < 17:
            await self._migrate_v17()
            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (17)"
            )
            version = 17

        if version < 18:
            await self._migrate_v18()
            await self._conn.execute(
                "INSERT INTO schema_version(version) VALUES (18)"
            )
            version = 18

        if version < 19:
            await self._migrate_v19()
            await self._conn.execute("INSERT INTO schema_version(version) VALUES (19)")
            version = 19

        if version < 20:
            await self._migrate_v20()
            await self._conn.execute("INSERT INTO schema_version(version) VALUES (20)")
            version = 20

        if version < 21:
            await self._migrate_v21()
            await self._conn.execute("INSERT INTO schema_version(version) VALUES (21)")
            version = 21

        if version < 22:
            await self._migrate_v22()
            await self._conn.execute("INSERT INTO schema_version(version) VALUES (22)")
            version = 22

        await self._conn.commit()

    async def _migrate_v22(self) -> None:
        """Task-level learning. Preserve legacy rows; never destroy user history."""
        assert self._conn is not None
        cursor = await self._conn.execute("PRAGMA table_info(tasks)")
        columns = {row[1] for row in await cursor.fetchall()}
        for column, definition in (
            ("updated_at", "TEXT"),
            ("scope", "TEXT NOT NULL DEFAULT 'local'"),
            ("checkpoint_seq", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in columns:
                await self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")
        await self._conn.execute(
            "UPDATE tasks SET updated_at=created_at WHERE updated_at IS NULL"
        )
        cursor = await self._conn.execute("PRAGMA table_info(episodes)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "logical_task_id" not in columns:
            await self._conn.execute("ALTER TABLE episodes ADD COLUMN logical_task_id TEXT")
        await self._conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_tasks_recent
                ON tasks(user_id, scope, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_trajectories_task_recent
                ON trajectories(task_id, created_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_logical_task
                ON episodes(logical_task_id) WHERE logical_task_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS task_learning_jobs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                watermark INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT 'completion',
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','processing','completed','failed','skipped')),
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                lease_until TEXT,
                result_json TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(task_id, watermark, reason)
            );
            CREATE INDEX IF NOT EXISTS idx_task_learning_due
                ON task_learning_jobs(status,next_attempt_at,created_at);
            CREATE TABLE IF NOT EXISTS learned_skill_sources (
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                candidate_id TEXT NOT NULL REFERENCES skill_candidates(id) ON DELETE CASCADE,
                fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(task_id,fingerprint)
            );
        """)

    async def _migrate_v21(self) -> None:
        """Preserve original retrieval timestamps and count actual review claims."""
        assert self._conn is not None
        cursor = await self._conn.execute("PRAGMA table_info(memory_usage)")
        fields = {str(row[1]) for row in await cursor.fetchall()}
        if "first_retrieved_at" not in fields:
            await self._conn.execute("ALTER TABLE memory_usage ADD COLUMN first_retrieved_at TEXT")
        if "last_retrieved_at" not in fields:
            await self._conn.execute("ALTER TABLE memory_usage ADD COLUMN last_retrieved_at TEXT")
        if "retrieval_count" not in fields:
            await self._conn.execute(
                "ALTER TABLE memory_usage ADD COLUMN retrieval_count INTEGER NOT NULL DEFAULT 1"
            )
        await self._conn.execute(
            """UPDATE memory_usage SET first_retrieved_at=COALESCE(first_retrieved_at,retrieved_at),
                last_retrieved_at=COALESCE(last_retrieved_at,retrieved_at)"""
        )
        await self._conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_memory_usage_recent_v21
                ON memory_usage(tier, item_id, last_retrieved_at DESC);
            CREATE TABLE IF NOT EXISTS reflection_attempts (
                job_id TEXT NOT NULL REFERENCES reflection_jobs(id) ON DELETE CASCADE,
                attempt_no INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                PRIMARY KEY(job_id, attempt_no)
            );
            CREATE INDEX IF NOT EXISTS idx_reflection_attempts_day
                ON reflection_attempts(started_at);
        """)
        # Historical attempts have no reliable timestamps. Do not charge them
        # to today's budget or invent an attempt-time history.

    async def _migrate_v20(self) -> None:
        """Non-destructive retrieval attribution, conflicts, and curator findings."""
        assert self._conn is not None
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tier TEXT NOT NULL, item_id TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL, scope TEXT NOT NULL,
                thread_id TEXT NOT NULL, retrieved_at TEXT NOT NULL,
                UNIQUE(tier, item_id, version, thread_id)
            );
            CREATE INDEX IF NOT EXISTS idx_memory_usage_last
                ON memory_usage(tier, item_id, retrieved_at DESC);
            CREATE TABLE IF NOT EXISTS memory_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                left_ref TEXT NOT NULL, right_ref TEXT NOT NULL,
                predicate TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open', 'resolved')),
                preferred_ref TEXT,
                created_at TEXT NOT NULL, resolved_at TEXT,
                UNIQUE(left_ref, right_ref)
            );
            CREATE TABLE IF NOT EXISTS memory_curator_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                finding_type TEXT NOT NULL,
                item_type TEXT NOT NULL, item_id TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'local',
                scope TEXT NOT NULL DEFAULT 'local',
                summary TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open', 'dismissed', 'resolved')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(finding_type, item_type, item_id)
            );
            CREATE INDEX IF NOT EXISTS idx_curator_open
                ON memory_curator_findings(user_id, scope, status);
        """)
        # Keep v19's status CHECK unchanged. Archival is a separate flag so
        # older procedure review code cannot accidentally publish archives.
        cursor = await self._conn.execute("PRAGMA table_info(procedure_drafts)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "archived_at" not in columns:
            await self._conn.execute("ALTER TABLE procedure_drafts ADD COLUMN archived_at TEXT")

    async def _migrate_v19(self) -> None:
        """Reviewable procedural suggestions, evidence and draft history."""
        assert self._conn is not None
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS procedure_drafts (
                id TEXT PRIMARY KEY,
                source_trajectory_id TEXT NOT NULL REFERENCES trajectories(id) ON DELETE CASCADE,
                trigger TEXT NOT NULL CHECK(trigger IN ('feedback','reflection')),
                parent_id TEXT REFERENCES procedure_drafts(id) ON DELETE SET NULL,
                user_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('new','revision')),
                target_skill_name TEXT,
                base_skill_version TEXT,
                title TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                rationale TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'needs_review'
                    CHECK(status IN ('needs_review','approved','rejected','candidate_created')),
                version INTEGER NOT NULL DEFAULT 1,
                user_confirmed INTEGER NOT NULL DEFAULT 0,
                candidate_id TEXT REFERENCES skill_candidates(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_trajectory_id, trigger)
            );
            CREATE INDEX IF NOT EXISTS idx_procedure_drafts_status
                ON procedure_drafts(status, updated_at DESC);
            CREATE TABLE IF NOT EXISTS procedure_draft_revisions (
                draft_id TEXT NOT NULL REFERENCES procedure_drafts(id) ON DELETE CASCADE,
                version INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (draft_id, version)
            );
        """)

    async def _migrate_v18(self) -> None:
        """Persist bounded reflection jobs and provenance of reviewer output."""
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reflection_jobs (
                id TEXT PRIMARY KEY,
                trajectory_id TEXT NOT NULL REFERENCES trajectories(id) ON DELETE CASCADE,
                reason TEXT NOT NULL CHECK(reason IN ('completion', 'feedback')),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'processing', 'completed', 'skipped', 'failed')),
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                lease_until TEXT,
                result_json TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(trajectory_id, reason)
            );
            CREATE INDEX IF NOT EXISTS idx_reflection_jobs_due
              ON reflection_jobs(status, next_attempt_at, created_at);
            CREATE INDEX IF NOT EXISTS idx_reflection_jobs_trajectory
              ON reflection_jobs(trajectory_id);
            """
        )

    async def _migrate_v17(self) -> None:
        """Durable episodic records and transactionally maintained FTS5 index."""
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                source_trajectory_id TEXT NOT NULL UNIQUE
                    REFERENCES trajectories(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'local',
                thread_id TEXT,
                goal TEXT NOT NULL,
                summary TEXT NOT NULL,
                outcome TEXT NOT NULL,
                outcome_verified INTEGER NOT NULL DEFAULT 0,
                tool_names TEXT NOT NULL DEFAULT '[]',
                evidence TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_episodes_scope_recent
                ON episodes(user_id, scope, created_at DESC);
            CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                goal, summary, content='episodes', content_rowid='seq',
                tokenize='porter'
            );
            CREATE TRIGGER IF NOT EXISTS episodes_fts_insert AFTER INSERT ON episodes
            BEGIN
                INSERT INTO episodes_fts(rowid, goal, summary)
                VALUES (new.seq, new.goal, new.summary);
            END;
            CREATE TRIGGER IF NOT EXISTS episodes_fts_delete AFTER DELETE ON episodes
            BEGIN
                INSERT INTO episodes_fts(episodes_fts, rowid, goal, summary)
                VALUES ('delete', old.seq, old.goal, old.summary);
            END;
            CREATE TRIGGER IF NOT EXISTS episodes_fts_update AFTER UPDATE ON episodes
            BEGIN
                INSERT INTO episodes_fts(episodes_fts, rowid, goal, summary)
                VALUES ('delete', old.seq, old.goal, old.summary);
                INSERT INTO episodes_fts(rowid, goal, summary)
                VALUES (new.seq, new.goal, new.summary);
            END;
            """
        )

    async def _migrate_v16(self) -> None:
        """Separate observed completion metrics from verified task quality."""
        assert self._conn is not None
        await self._conn.execute(
            """ALTER TABLE skill_execution_metrics
               ADD COLUMN outcome_verified INTEGER NOT NULL DEFAULT 1"""
        )
        # v13-v15 samples were deliberately graded on insert and remain
        # backward-compatible. New completed (unconfirmed) runs use 0.

    async def _migrate_v15(self) -> None:
        """Append-only trajectories and opt-in, evidence-linked experience."""
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trajectory_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                trajectory_id TEXT NOT NULL REFERENCES trajectories(id) ON DELETE CASCADE,
                occurred_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                source TEXT,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_trajectory_events_run
                ON trajectory_events(trajectory_id, seq);

            CREATE TABLE IF NOT EXISTS learned_experiences (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('preference', 'correction', 'procedure')),
                status TEXT NOT NULL CHECK(status IN ('active', 'needs_review', 'rejected')),
                scope TEXT NOT NULL DEFAULT 'local',
                fingerprint TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.0,
                version INTEGER NOT NULL DEFAULT 1,
                source_trajectory_id TEXT REFERENCES trajectories(id),
                evidence TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(scope, kind, fingerprint)
            );
            CREATE INDEX IF NOT EXISTS idx_experience_active
                ON learned_experiences(scope, status, kind, updated_at);
            CREATE TABLE IF NOT EXISTS experience_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experience_id TEXT NOT NULL REFERENCES learned_experiences(id),
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                evidence TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

    async def _migrate_v1(self) -> None:
        """Schema v1: tasks, trajectories, skills, memories metadata."""
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                thread_id TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                goal TEXT,
                plan TEXT,
                result TEXT,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS trajectories (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id),
                created_at TEXT NOT NULL,
                steps TEXT,       -- JSON list of execution steps
                outcome TEXT,     -- success / failure
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'candidate',
                path TEXT,
                created_at TEXT NOT NULL,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                tier TEXT NOT NULL,          -- semantic / episodic / procedural
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);
            CREATE INDEX IF NOT EXISTS idx_trajectories_task ON trajectories(task_id);
            CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
            CREATE INDEX IF NOT EXISTS idx_memories_ns ON memories(namespace);
            """
        )

    async def _migrate_v2(self) -> None:
        """Schema v2: production chat persistence."""

        assert self._conn is not None

        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL UNIQUE,

                title TEXT,
                model TEXT NOT NULL,

                archived INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,

                conversation_id TEXT NOT NULL
                    REFERENCES conversations(id)
                    ON DELETE CASCADE,

                role TEXT NOT NULL,
                content TEXT NOT NULL,

                status TEXT NOT NULL DEFAULT 'complete',

                parent_message_id TEXT,

                created_at TEXT NOT NULL,

                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,

                conversation_id TEXT NOT NULL
                    REFERENCES conversations(id)
                    ON DELETE CASCADE,

                message_id TEXT
                    REFERENCES chat_messages(id)
                    ON DELETE SET NULL,

                filename TEXT NOT NULL,
                mime_type TEXT,
                kind TEXT NOT NULL,

                virtual_path TEXT NOT NULL,
                extracted_virtual_path TEXT,

                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,

                status TEXT NOT NULL,

                created_at TEXT NOT NULL,

                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS
                idx_conversations_updated
            ON conversations(updated_at);

            CREATE INDEX IF NOT EXISTS
                idx_chat_messages_conversation
            ON chat_messages(conversation_id, created_at);

            CREATE INDEX IF NOT EXISTS
                idx_attachments_conversation
            ON attachments(conversation_id);

            CREATE INDEX IF NOT EXISTS
                idx_attachments_message
            ON attachments(message_id);
            """
        )


    async def _migrate_v3(self) -> None:
        """Schema v3: chat branching + attachment RAG indexes."""

        assert self._conn is not None

        # SQLite has no portable ADD COLUMN IF NOT EXISTS, so check first.
        conversation_cols = {
            row[1]
            for row in await (await self._conn.execute(
                "PRAGMA table_info(conversations)"
            )).fetchall()
        }
        if "active_branch_id" not in conversation_cols:
            await self._conn.execute(
                "ALTER TABLE conversations ADD COLUMN active_branch_id TEXT"
            )

        message_cols = {
            row[1]
            for row in await (await self._conn.execute(
                "PRAGMA table_info(chat_messages)"
            )).fetchall()
        }
        if "revision_of" not in message_cols:
            await self._conn.execute(
                "ALTER TABLE chat_messages ADD COLUMN revision_of TEXT"
            )
        if "base_checkpoint_id" not in message_cols:
            await self._conn.execute(
                "ALTER TABLE chat_messages ADD COLUMN base_checkpoint_id TEXT"
            )
        if "checkpoint_id" not in message_cols:
            await self._conn.execute(
                "ALTER TABLE chat_messages ADD COLUMN checkpoint_id TEXT"
            )

        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_branches (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL
                    REFERENCES conversations(id)
                    ON DELETE CASCADE,
                thread_id TEXT NOT NULL,
                parent_branch_id TEXT
                    REFERENCES chat_branches(id)
                    ON DELETE SET NULL,
                fork_message_id TEXT
                    REFERENCES chat_messages(id)
                    ON DELETE SET NULL,
                fork_checkpoint_id TEXT,
                head_checkpoint_id TEXT,
                label TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS branch_messages (
                branch_id TEXT NOT NULL
                    REFERENCES chat_branches(id)
                    ON DELETE CASCADE,
                message_id TEXT NOT NULL
                    REFERENCES chat_messages(id)
                    ON DELETE CASCADE,
                position INTEGER NOT NULL,
                PRIMARY KEY (branch_id, position),
                UNIQUE (branch_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS message_attachments (
                message_id TEXT NOT NULL
                    REFERENCES chat_messages(id)
                    ON DELETE CASCADE,
                attachment_id TEXT NOT NULL
                    REFERENCES attachments(id)
                    ON DELETE CASCADE,
                PRIMARY KEY (message_id, attachment_id)
            );

            CREATE TABLE IF NOT EXISTS attachment_chunks (
                id TEXT PRIMARY KEY,
                attachment_id TEXT NOT NULL
                    REFERENCES attachments(id)
                    ON DELETE CASCADE,
                conversation_id TEXT NOT NULL
                    REFERENCES conversations(id)
                    ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                start_char INTEGER NOT NULL,
                end_char INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (attachment_id, chunk_index)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS attachment_chunks_fts USING fts5(
                attachment_id UNINDEXED,
                conversation_id UNINDEXED,
                chunk_id UNINDEXED,
                chunk_index UNINDEXED,
                content,
                tokenize='porter'
            );

            CREATE INDEX IF NOT EXISTS idx_chat_branches_conversation
                ON chat_branches(conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_branch_messages_message
                ON branch_messages(message_id);
            CREATE INDEX IF NOT EXISTS idx_message_attachments_attachment
                ON message_attachments(attachment_id);
            CREATE INDEX IF NOT EXISTS idx_attachment_chunks_attachment
                ON attachment_chunks(attachment_id, chunk_index);
            CREATE INDEX IF NOT EXISTS idx_attachment_chunks_conversation
                ON attachment_chunks(conversation_id);
            """
        )

        # Bootstrap pre-v3 conversations into a main branch without changing
        # their existing message rows.
        conversations = await (await self._conn.execute(
            "SELECT id, thread_id, created_at, active_branch_id FROM conversations"
        )).fetchall()

        for conversation in conversations:
            branch_id = conversation[3] or f"{conversation[0]}:main"
            await self._conn.execute(
                """
                INSERT OR IGNORE INTO chat_branches(
                    id, conversation_id, thread_id, parent_branch_id, fork_message_id,
                    fork_checkpoint_id, head_checkpoint_id, label, created_at
                ) VALUES (?, ?, ?, NULL, NULL, NULL, NULL, 'main', ?)
                """,
                (branch_id, conversation[0], conversation[1], conversation[2]),
            )
            await self._conn.execute(
                "UPDATE conversations SET active_branch_id = ? WHERE id = ?",
                (branch_id, conversation[0]),
            )

            existing = await (await self._conn.execute(
                "SELECT 1 FROM branch_messages WHERE branch_id = ? LIMIT 1",
                (branch_id,),
            )).fetchone()
            if existing is None:
                messages = await (await self._conn.execute(
                    """
                    SELECT id FROM chat_messages
                    WHERE conversation_id = ?
                    ORDER BY created_at ASC, rowid ASC
                    """,
                    (conversation[0],),
                )).fetchall()
                for position, message in enumerate(messages):
                    await self._conn.execute(
                        """
                        INSERT OR IGNORE INTO branch_messages(
                            branch_id, message_id, position
                        ) VALUES (?, ?, ?)
                        """,
                        (branch_id, message[0], position),
                    )

        # Preserve old one-message attachment ownership in the new many-to-many
        # relation so attachments can be reused by edited/resend branches.
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO message_attachments(message_id, attachment_id)
            SELECT message_id, id FROM attachments WHERE message_id IS NOT NULL
            """
        )


    async def _migrate_v4(self) -> None:
        """Schema v4: explicit LangGraph thread per chat branch.

        Branches that fork before a real checkpoint (for example editing the
        first user turn) need a fresh LangGraph thread. Later forks can safely
        reuse the parent thread together with a checkpoint_id.
        """

        assert self._conn is not None

        branch_cols = {
            row[1]
            for row in await (await self._conn.execute(
                "PRAGMA table_info(chat_branches)"
            )).fetchall()
        }

        if "thread_id" not in branch_cols:
            await self._conn.execute(
                "ALTER TABLE chat_branches ADD COLUMN thread_id TEXT"
            )

        await self._conn.execute(
            """
            UPDATE chat_branches
            SET thread_id = (
                SELECT conversations.thread_id
                FROM conversations
                WHERE conversations.id = chat_branches.conversation_id
            )
            WHERE thread_id IS NULL OR thread_id = ''
            """
        )

    async def _migrate_v5(self) -> None:
        """Schema v5: personal-agent tasks, schedules, notifications and audit."""

        assert self._conn is not None

        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS personal_tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT NOT NULL DEFAULT 'normal',
                due_at TEXT,
                tags TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                schedule_type TEXT NOT NULL,
                schedule_expr TEXT NOT NULL,
                timezone TEXT NOT NULL DEFAULT 'UTC',
                next_run_at TEXT,
                last_run_at TEXT,
                conversation_id TEXT,
                last_result TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                created_at TEXT NOT NULL,
                read_at TEXT,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS skill_candidates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'candidate',
                source_trajectory_ids TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS tool_audit (
                id TEXT PRIMARY KEY,
                run_id TEXT,
                tool_name TEXT NOT NULL,
                risk TEXT NOT NULL,
                arguments TEXT,
                result TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_personal_tasks_status
                ON personal_tasks(status, due_at);
            CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_next_run
                ON scheduled_jobs(enabled, next_run_at);
            CREATE INDEX IF NOT EXISTS idx_notifications_created
                ON notifications(created_at);
            CREATE INDEX IF NOT EXISTS idx_skill_candidates_name
                ON skill_candidates(name, status);
            CREATE INDEX IF NOT EXISTS idx_tool_audit_name
                ON tool_audit(tool_name, created_at);
            """
        )

    async def _migrate_v6(self) -> None:
        """Schema v6: durable Deep Agents human-in-the-loop approvals."""
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pending_approvals (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL UNIQUE REFERENCES conversations(id) ON DELETE CASCADE,
                branch_id TEXT NOT NULL REFERENCES chat_branches(id) ON DELETE CASCADE,
                thread_id TEXT NOT NULL,
                checkpoint_id TEXT NOT NULL,
                user_message_id TEXT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
                model_name TEXT NOT NULL,
                interrupt_data TEXT NOT NULL,
                partial_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pending_approvals_branch
                ON pending_approvals(branch_id);
            """
        )

    async def _migrate_v7(self) -> None:
        """Schema v7: immutable skill versions + baseline/candidate evaluations."""
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS skill_versions (
                id TEXT PRIMARY KEY,
                skill_name TEXT NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'staged',
                bundle_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                source_candidate_id TEXT,
                source_evaluation_id TEXT,
                created_at TEXT NOT NULL,
                metadata TEXT,
                UNIQUE(skill_name, version)
            );

            CREATE TABLE IF NOT EXISTS skill_evaluations (
                id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                verdict TEXT,
                baseline_metrics TEXT,
                candidate_metrics TEXT,
                comparison TEXT,
                case_results TEXT,
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_skill_versions_name
                ON skill_versions(skill_name, created_at);
            CREATE INDEX IF NOT EXISTS idx_skill_versions_status
                ON skill_versions(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_skill_evaluations_candidate
                ON skill_evaluations(candidate_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_skill_evaluations_name
                ON skill_evaluations(skill_name, created_at);
            """
        )

    async def _migrate_v8(self) -> None:
        """Schema v8: immutable trajectory replay fixture manifests."""
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trajectory_replay_fixtures (
                id TEXT PRIMARY KEY,
                trajectory_id TEXT NOT NULL UNIQUE REFERENCES trajectories(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                complete INTEGER NOT NULL DEFAULT 0,
                file_count INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                manifest_json TEXT NOT NULL,
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_replay_fixtures_trajectory
                ON trajectory_replay_fixtures(trajectory_id);
            CREATE INDEX IF NOT EXISTS idx_replay_fixtures_complete
                ON trajectory_replay_fixtures(complete, created_at);
            """
        )

    async def _migrate_v9(self) -> None:
        """Schema v9: persistent action receipts and independent connector readbacks."""
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS action_receipts (
                id TEXT PRIMARY KEY,
                connector TEXT NOT NULL,
                action_tool TEXT NOT NULL,
                resource_type TEXT,
                resource_id TEXT,
                status TEXT NOT NULL,
                action_args TEXT,
                action_result TEXT,
                readback_tool TEXT,
                readback_args TEXT,
                readback_result TEXT,
                comparison_result TEXT,
                created_at TEXT NOT NULL,
                verified_at TEXT,
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_action_receipts_status
                ON action_receipts(status, created_at);

            CREATE INDEX IF NOT EXISTS idx_action_receipts_resource
                ON action_receipts(connector, resource_id);

            CREATE INDEX IF NOT EXISTS idx_action_receipts_tool
                ON action_receipts(connector, action_tool, created_at);
            """
        )

    async def _migrate_v10(self) -> None:
        """Schema v10: user-configurable MCP server and tool enable/disable preferences."""
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS mcp_server_preferences (
                server_name TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mcp_tool_preferences (
                server_name TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (server_name, tool_name)
            );

            CREATE INDEX IF NOT EXISTS idx_mcp_tool_preferences_server
            ON mcp_tool_preferences(server_name);
            """
        )

    async def _migrate_v11(self) -> None:
        """Schema v11: agent permission preferences and user-controlled agent settings."""

        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_permissions (
                permission_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL
                    CHECK (
                        mode IN ('allow', 'ask', 'deny')
                    ),
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    async def _migrate_v12(self) -> None:
        """Historical v12 schema for retired learning coordinator state.

        Keep the migration for existing user databases and schema-version
        continuity; it does not enable or start the retired worker.
        """

        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS skill_learning_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),

                success_count_checkpoint INTEGER NOT NULL DEFAULT 0,

                last_run_id TEXT,

                last_started_at TEXT,
                last_completed_at TEXT,

                last_status TEXT NOT NULL DEFAULT 'never',
                last_error TEXT,

                last_created_count INTEGER NOT NULL DEFAULT 0,
                last_evaluated_count INTEGER NOT NULL DEFAULT 0,
                last_verified_count INTEGER NOT NULL DEFAULT 0,
                last_promoted_count INTEGER NOT NULL DEFAULT 0,

                updated_at TEXT NOT NULL
            );

            INSERT OR IGNORE INTO skill_learning_state(
                id,
                success_count_checkpoint,
                last_status,
                updated_at
            )
            VALUES (
                1,
                0,
                'never',
                CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS skill_learning_runs (
                id TEXT PRIMARY KEY,

                started_at TEXT NOT NULL,
                completed_at TEXT,

                status TEXT NOT NULL,

                checkpoint_before INTEGER NOT NULL,
                observed_success_count INTEGER NOT NULL,

                created_count INTEGER NOT NULL DEFAULT 0,
                evaluated_count INTEGER NOT NULL DEFAULT 0,
                verified_count INTEGER NOT NULL DEFAULT 0,
                promoted_count INTEGER NOT NULL DEFAULT 0,

                error TEXT,
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS
                idx_skill_learning_runs_started
            ON skill_learning_runs(started_at DESC);
            """
        )

    async def _migrate_v13(self) -> None:
        """Schema v13: execution metrics, regression log and A/B experiments."""

        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS skill_execution_metrics (
                id TEXT PRIMARY KEY,

                skill_name TEXT NOT NULL,
                skill_version TEXT NOT NULL,

                trajectory_id TEXT,

                success INTEGER NOT NULL DEFAULT 0,
                latency_ms REAL DEFAULT 0,
                tokens_used INTEGER DEFAULT 0,
                tool_failures INTEGER DEFAULT 0,

                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS
                idx_skill_metrics_lookup
            ON skill_execution_metrics(
                skill_name,
                skill_version
            );

            CREATE TABLE IF NOT EXISTS skill_regressions (
                id TEXT PRIMARY KEY,

                skill_name TEXT NOT NULL,
                bad_version TEXT NOT NULL,
                stable_version TEXT NOT NULL,

                reason TEXT NOT NULL,
                severity TEXT NOT NULL,

                rolled_back INTEGER DEFAULT 0,

                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skill_experiments (
                id TEXT PRIMARY KEY,

                skill_name TEXT NOT NULL,
                control_version TEXT NOT NULL,
                experiment_version TEXT NOT NULL,

                traffic_percent INTEGER NOT NULL,
                status TEXT NOT NULL,

                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS
                idx_skill_experiments_lookup
            ON skill_experiments(skill_name, status);
            """
        )

    async def _migrate_v14(self) -> None:
        """Schema v14: experiment arms/assignments, runtime detail, dependencies.

        v12 and v13 already created ``skill_experiments`` and
        ``skill_regressions`` with a narrower shape, so those tables are
        widened with ``ALTER TABLE`` instead of being dropped and rebuilt.
        Every step is guarded, making a re-run after a partial failure a
        no-op rather than an error.
        """

        assert self._conn is not None

        async def add_columns(table: str, columns: dict[str, str]) -> None:
            existing = {
                row[1]
                for row in await (
                    await self._conn.execute(f"PRAGMA table_info({table})")  # noqa: S608
                ).fetchall()
            }

            for name, declaration in columns.items():
                if name not in existing:
                    await self._conn.execute(
                        f"ALTER TABLE {table} "  # noqa: S608
                        f"ADD COLUMN {name} {declaration}"
                    )

        await add_columns(
            "skill_learning_runs",
            {"experiment_count": "INTEGER NOT NULL DEFAULT 0"},
        )

        await add_columns(
            "skill_experiments",
            {
                "strategy": "TEXT NOT NULL DEFAULT 'ab'",
                "min_samples_per_arm": "INTEGER NOT NULL DEFAULT 20",
                "max_samples_total": "INTEGER NOT NULL DEFAULT 200",
                "alpha": "REAL NOT NULL DEFAULT 0.05",
                "bayesian_threshold": "REAL NOT NULL DEFAULT 0.95",
                "minimum_effect": "REAL NOT NULL DEFAULT 0.03",
                "harm_effect": "REAL NOT NULL DEFAULT 0.05",
                "bayesian_draws": "INTEGER NOT NULL DEFAULT 5000",
                "auto_stop": "INTEGER NOT NULL DEFAULT 1",
                "auto_promote": "INTEGER NOT NULL DEFAULT 1",
                "winner_version": "TEXT",
                "reason": "TEXT",
                "completed_at": "TEXT",
                "metadata": "TEXT",
            },
        )

        await add_columns(
            "skill_execution_metrics",
            {
                "experiment_id": "TEXT",
                "arm_kind": "TEXT NOT NULL DEFAULT 'active'",
                "unit_id": "TEXT",
                "duration_seconds": "REAL",
                "input_tokens": "INTEGER NOT NULL DEFAULT 0",
                "output_tokens": "INTEGER NOT NULL DEFAULT 0",
                "tool_calls": "INTEGER NOT NULL DEFAULT 0",
                "tool_errors": "INTEGER NOT NULL DEFAULT 0",
                "completed_at": "TEXT",
                "metadata": "TEXT",
            },
        )

        await add_columns(
            "skill_regressions",
            {
                "reasons": "TEXT NOT NULL DEFAULT ''",
                "evidence": "TEXT NOT NULL DEFAULT ''",
                "rollback_error": "TEXT",
            },
        )

        # Samples recorded before v14 are complete by definition — they were
        # written after the run ended — so backfill the completion timestamp
        # rather than letting them fall out of "completed" queries.
        await self._conn.execute(
            """
            UPDATE skill_execution_metrics
            SET completed_at = created_at
            WHERE completed_at IS NULL
            """
        )

        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS skill_experiment_arms (
                experiment_id TEXT NOT NULL
                    REFERENCES skill_experiments(id)
                    ON DELETE CASCADE,

                version TEXT NOT NULL,

                candidate_id TEXT,

                is_control INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL,

                metadata TEXT,

                PRIMARY KEY(experiment_id, version)
            );

            CREATE TABLE IF NOT EXISTS skill_experiment_assignments (
                experiment_id TEXT NOT NULL
                    REFERENCES skill_experiments(id)
                    ON DELETE CASCADE,

                unit_id TEXT NOT NULL,

                version TEXT NOT NULL,

                assigned_at TEXT NOT NULL,

                PRIMARY KEY(experiment_id, unit_id)
            );

            CREATE TABLE IF NOT EXISTS skill_dependencies (
                skill_name TEXT NOT NULL,

                depends_on_skill TEXT NOT NULL,

                version_constraint TEXT NOT NULL DEFAULT '*',

                required INTEGER NOT NULL DEFAULT 1,

                created_at TEXT NOT NULL,

                metadata TEXT,

                PRIMARY KEY(skill_name, depends_on_skill),

                CHECK(skill_name <> depends_on_skill)
            );

            CREATE INDEX IF NOT EXISTS
                idx_skill_dependencies_target
            ON skill_dependencies(depends_on_skill);

            CREATE INDEX IF NOT EXISTS
                idx_skill_experiments_one_running
            ON skill_experiments(skill_name)
            WHERE status = 'running';

            CREATE INDEX IF NOT EXISTS
                idx_skill_regressions_name
            ON skill_regressions(skill_name, created_at DESC);

            CREATE INDEX IF NOT EXISTS
                idx_skill_metrics_runtime
            ON skill_execution_metrics(
                skill_name,
                skill_version,
                completed_at
            );

            CREATE INDEX IF NOT EXISTS
                idx_skill_metrics_experiment
            ON skill_execution_metrics(
                experiment_id,
                skill_version,
                completed_at
            );

            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_skill_metrics_one_per_trajectory
            ON skill_execution_metrics(trajectory_id, skill_name)
            WHERE trajectory_id IS NOT NULL;
            """
        )

    # -- execution helpers -------------------------------------------------

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        """Run a single SQL statement. Use `executescript` for multi-statement DDL."""
        assert self._conn is not None, "call open() first"
        async with self._write_lock:
            cur = await self._conn.execute(sql, params)
            await self._conn.commit()
            return cur

    async def executescript(self, sql: str) -> None:
        """Run a multi-statement SQL script (DDL with several statements)."""
        assert self._conn is not None, "call open() first"
        await self._conn.executescript(sql)
        await self._conn.commit()

    async def execute_returning(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        """Run a mutating RETURNING statement and commit after consuming rows."""
        assert self._conn is not None, "call open() first"
        async with self._write_lock:
            cur = await self._conn.execute(sql, params)
            rows = await cur.fetchall()
            await cur.close()
            await self._conn.commit()
            return [dict(row) for row in rows]

    async def fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        assert self._conn is not None, "call open() first"
        cur = await self._conn.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = await self.fetch(sql, params)
        return rows[0] if rows else None

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "SQLiteDatabase":
        await self.open()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()