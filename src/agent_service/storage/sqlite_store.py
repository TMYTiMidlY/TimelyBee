from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

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
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS channel_offsets (
                    channel TEXT PRIMARY KEY,
                    offset_value TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await self._ensure_messages_json_column(conn)
            await conn.commit()

    async def _ensure_messages_json_column(self, conn: aiosqlite.Connection) -> None:
        cursor = await conn.execute("PRAGMA table_info(agent_runs)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "messages_json" not in columns:
            await conn.execute("ALTER TABLE agent_runs ADD COLUMN messages_json TEXT")

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
                    channel, conversation_id, sender_id, message_id, text, attachments_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inbound.channel,
                    inbound.conversation_id,
                    inbound.sender_id,
                    inbound.message_id,
                    inbound.text,
                    json.dumps([att.__dict__ for att in inbound.attachments], ensure_ascii=False),
                    json.dumps(inbound.raw, ensure_ascii=False),
                ),
            )
            await conn.commit()
            return cur.rowcount > 0

    async def load_history(self, channel: str, conversation_id: str) -> list[ModelMessage]:
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                SELECT messages_json
                FROM agent_runs
                WHERE channel = ? AND conversation_id = ?
                  AND messages_json IS NOT NULL AND messages_json != ''
                ORDER BY id DESC
                LIMIT 1
                """,
                (channel, conversation_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return []
        return ModelMessagesTypeAdapter.validate_json(row[0])

    async def save_agent_reply(self, inbound: InboundMessage, reply: AgentReply) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO messages (
                    channel, conversation_id, sender_id, message_id, text, attachments_json, raw_json
                ) VALUES (?, ?, 'agent', ?, ?, ?, ?)
                """,
                (
                    inbound.channel,
                    inbound.conversation_id,
                    f"agent:{inbound.message_id}",
                    reply.text,
                    json.dumps([att.__dict__ for att in reply.attachments], ensure_ascii=False),
                    json.dumps(reply.metadata, ensure_ascii=False),
                ),
            )
            await conn.execute(
                """
                INSERT INTO agent_runs (
                    channel, conversation_id, inbound_message_id, reply_text, metadata_json, messages_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    inbound.channel,
                    inbound.conversation_id,
                    inbound.message_id,
                    reply.text,
                    json.dumps(reply.metadata, ensure_ascii=False),
                    reply.model_messages_json or "",
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
