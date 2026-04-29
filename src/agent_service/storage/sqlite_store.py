from __future__ import annotations

import json
import uuid
from pathlib import Path

import aiosqlite
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from agent_service.assistant.session import SessionState
from agent_service.types import AgentReply, InboundMessage


class SQLiteStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(channel, conversation_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    attachments_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    context_generation INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(channel, conversation_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    inbound_message_id TEXT NOT NULL,
                    reply_text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    messages_json TEXT,
                    context_generation INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS channel_offsets (
                    channel TEXT PRIMARY KEY,
                    offset_value TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS session_state (
                    channel TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    short_context_generation INTEGER NOT NULL DEFAULT 0,
                    model_provider TEXT,
                    model_name TEXT,
                    codex_mode INTEGER NOT NULL DEFAULT 0,
                    codex_thread_id TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(channel, conversation_id)
                );
                CREATE TABLE IF NOT EXISTS calendar_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    starts_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS background_tasks (
                    task_id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    context_generation INTEGER NOT NULL,
                    model_provider TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    description TEXT NOT NULL,
                    result_text TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await self._ensure_column(conn, "messages", "context_generation", "INTEGER NOT NULL DEFAULT 0")
            await self._ensure_column(conn, "agent_runs", "messages_json", "TEXT")
            await self._ensure_column(conn, "agent_runs", "context_generation", "INTEGER NOT NULL DEFAULT 0")
            await conn.commit()

    async def _ensure_column(self, conn: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
        cursor = await conn.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await cursor.fetchall()}
        if column not in columns:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def insert_inbound_message(self, inbound: InboundMessage) -> bool:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO conversations (channel, conversation_id)
                VALUES (?, ?)
                """,
                (inbound.channel, inbound.conversation_id),
            )
            cur = await conn.execute(
                """
                INSERT OR IGNORE INTO messages (
                    channel, conversation_id, sender_id, message_id, text,
                    attachments_json, raw_json, context_generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inbound.channel,
                    inbound.conversation_id,
                    inbound.sender_id,
                    inbound.message_id,
                    inbound.text,
                    json.dumps([att.__dict__ for att in inbound.attachments], ensure_ascii=False),
                    json.dumps(inbound.raw, ensure_ascii=False),
                    int(inbound.raw.get("_context_generation", 0)),
                ),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def load_history(
        self,
        channel: str,
        conversation_id: str,
        context_generation: int | None = None,
    ) -> list[ModelMessage]:
        params: list[object] = [channel, conversation_id]
        generation_clause = ""
        if context_generation is not None:
            generation_clause = "AND context_generation = ?"
            params.append(context_generation)
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                f"""
                SELECT messages_json
                FROM agent_runs
                WHERE channel = ? AND conversation_id = ?
                  AND messages_json IS NOT NULL AND messages_json != '' AND messages_json != '[]'
                  {generation_clause}
                ORDER BY id DESC
                LIMIT 1
                """,
                tuple(params),
            )
            row = await cursor.fetchone()
        if row is None:
            return []
        return ModelMessagesTypeAdapter.validate_json(row[0])

    async def save_agent_reply(self, inbound: InboundMessage, reply: AgentReply) -> None:
        context_generation = int(reply.metadata.get("context_generation", inbound.raw.get("_context_generation", 0)))
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO messages (
                    channel, conversation_id, sender_id, message_id, text,
                    attachments_json, raw_json, context_generation
                ) VALUES (?, ?, 'agent', ?, ?, ?, ?, ?)
                """,
                (
                    inbound.channel,
                    inbound.conversation_id,
                    f"agent:{inbound.message_id}",
                    reply.text,
                    json.dumps([att.__dict__ for att in reply.attachments], ensure_ascii=False),
                    json.dumps(reply.metadata, ensure_ascii=False),
                    context_generation,
                ),
            )
            await conn.execute(
                """
                INSERT INTO agent_runs (
                    channel, conversation_id, inbound_message_id, reply_text,
                    metadata_json, messages_json, context_generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inbound.channel,
                    inbound.conversation_id,
                    inbound.message_id,
                    reply.text,
                    json.dumps(reply.metadata, ensure_ascii=False),
                    reply.model_messages_json or "",
                    context_generation,
                ),
            )
            await conn.commit()

    async def save_channel_offset(self, channel: str, offset_value: str) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO channel_offsets (channel, offset_value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(channel) DO UPDATE SET
                    offset_value = excluded.offset_value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (channel, offset_value),
            )
            await conn.commit()

    async def get_session_state(self, channel: str, conversation_id: str) -> SessionState:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO session_state (channel, conversation_id)
                VALUES (?, ?)
                """,
                (channel, conversation_id),
            )
            await conn.commit()
            cursor = await conn.execute(
                """
                SELECT short_context_generation, model_provider, model_name, codex_mode, codex_thread_id
                FROM session_state
                WHERE channel = ? AND conversation_id = ?
                """,
                (channel, conversation_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return SessionState(channel=channel, conversation_id=conversation_id)
        return SessionState(
            channel=channel,
            conversation_id=conversation_id,
            short_context_generation=int(row[0]),
            model_provider=row[1],
            model_name=row[2],
            codex_mode=bool(row[3]),
            codex_thread_id=row[4],
        )

    async def clear_short_context(self, channel: str, conversation_id: str) -> SessionState:
        await self.get_session_state(channel, conversation_id)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                UPDATE session_state
                SET short_context_generation = short_context_generation + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE channel = ? AND conversation_id = ?
                """,
                (channel, conversation_id),
            )
            await conn.commit()
        return await self.get_session_state(channel, conversation_id)

    async def set_session_model(self, channel: str, conversation_id: str, provider: str, model: str) -> SessionState:
        await self.get_session_state(channel, conversation_id)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                UPDATE session_state
                SET model_provider = ?, model_name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE channel = ? AND conversation_id = ?
                """,
                (provider, model, channel, conversation_id),
            )
            await conn.commit()
        return await self.get_session_state(channel, conversation_id)

    async def reset_session_model(self, channel: str, conversation_id: str) -> SessionState:
        await self.get_session_state(channel, conversation_id)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                UPDATE session_state
                SET model_provider = NULL, model_name = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE channel = ? AND conversation_id = ?
                """,
                (channel, conversation_id),
            )
            await conn.commit()
        return await self.get_session_state(channel, conversation_id)

    async def set_codex_mode(
        self,
        channel: str,
        conversation_id: str,
        *,
        enabled: bool,
        thread_id: str | None = None,
    ) -> SessionState:
        await self.get_session_state(channel, conversation_id)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                UPDATE session_state
                SET codex_mode = ?, codex_thread_id = COALESCE(?, codex_thread_id),
                    updated_at = CURRENT_TIMESTAMP
                WHERE channel = ? AND conversation_id = ?
                """,
                (1 if enabled else 0, thread_id, channel, conversation_id),
            )
            await conn.commit()
        return await self.get_session_state(channel, conversation_id)

    async def create_calendar_event(
        self,
        channel: str,
        conversation_id: str,
        *,
        user_id: str,
        title: str,
        raw_text: str,
        starts_at: str | None = None,
    ) -> int:
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                INSERT INTO calendar_events (
                    channel, conversation_id, user_id, title, raw_text, starts_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (channel, conversation_id, user_id, title, raw_text, starts_at),
            )
            await conn.commit()
            return int(cursor.lastrowid)

    async def list_calendar_events(self, channel: str, conversation_id: str, limit: int = 5) -> list[dict[str, str]]:
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                SELECT id, title, raw_text, starts_at, created_at
                FROM calendar_events
                WHERE channel = ? AND conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (channel, conversation_id, limit),
            )
            rows = await cursor.fetchall()
        return [
            {
                "id": str(row[0]),
                "title": row[1],
                "raw_text": row[2],
                "starts_at": row[3] or "",
                "created_at": row[4],
            }
            for row in rows
        ]

    async def create_background_task(
        self,
        channel: str,
        conversation_id: str,
        *,
        description: str,
        context_generation: int,
        model_provider: str,
        model_name: str,
    ) -> str:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO background_tasks (
                    task_id, channel, conversation_id, context_generation,
                    model_provider, model_name, status, description
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (task_id, channel, conversation_id, context_generation, model_provider, model_name, description),
            )
            await conn.commit()
        return task_id

    async def cancel_background_tasks(self, channel: str, conversation_id: str, task_id: str | None = None) -> int:
        params: list[object] = [channel, conversation_id]
        task_clause = ""
        if task_id:
            task_clause = "AND task_id = ?"
            params.append(task_id)
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                f"""
                UPDATE background_tasks
                SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                WHERE channel = ? AND conversation_id = ?
                  AND status IN ('pending', 'running')
                  {task_clause}
                """,
                tuple(params),
            )
            await conn.commit()
            return cursor.rowcount

    async def complete_background_task(self, task_id: str, result_text: str) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                UPDATE background_tasks
                SET status = 'completed', result_text = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
                """,
                (result_text, task_id),
            )
            await conn.commit()

    async def fail_background_task(self, task_id: str, error_text: str) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                UPDATE background_tasks
                SET status = 'failed', result_text = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
                """,
                (error_text, task_id),
            )
            await conn.commit()

    async def list_running_background_tasks(
        self,
        channel: str,
        conversation_id: str,
        task_id: str | None = None,
    ) -> list[dict[str, str]]:
        params: list[object] = [channel, conversation_id]
        task_clause = ""
        if task_id:
            task_clause = "AND task_id = ?"
            params.append(task_id)
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                f"""
                SELECT task_id, channel, conversation_id, context_generation,
                       model_provider, model_name, status, description
                FROM background_tasks
                WHERE channel = ? AND conversation_id = ?
                  AND status IN ('pending', 'running')
                  {task_clause}
                ORDER BY created_at ASC
                """,
                tuple(params),
            )
            rows = await cursor.fetchall()
        return [
            {
                "task_id": row[0],
                "channel": row[1],
                "conversation_id": row[2],
                "context_generation": str(row[3]),
                "model_provider": row[4],
                "model_name": row[5],
                "status": row[6],
                "description": row[7],
            }
            for row in rows
        ]
