"""SQLite database — Trajecta's own store (not Deep Agents memory).

Owns task metadata, trajectory metadata, skill registry, and config records.
Separate from `langgraph.db`, which LangGraph owns (checkpointer + long-term store).
Lives under `.trajecta/data/trajecta.db`.
"""

from __future__ import annotations

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

        await self._conn.commit()

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

    # -- execution helpers -------------------------------------------------

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        """Run a single SQL statement. Use `executescript` for multi-statement DDL."""
        assert self._conn is not None, "call open() first"
        cur = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cur

    async def executescript(self, sql: str) -> None:
        """Run a multi-statement SQL script (DDL with several statements)."""
        assert self._conn is not None, "call open() first"
        await self._conn.executescript(sql)
        await self._conn.commit()

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