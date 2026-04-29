from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from agent_service.background import BackgroundTaskManager
from agent_service.config.settings import Settings
from agent_service.storage.sqlite_store import SQLiteStore
from agent_service.types import AgentReply, InboundMessage


def _inbound() -> InboundMessage:
    return InboundMessage(
        channel="openilink",
        conversation_id="c1",
        sender_id="u1",
        message_id="m1",
        text="后台执行一次整理",
    )


@pytest.mark.asyncio
async def test_background_command_completes_and_reports_previous_task(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    await store.init()
    sent: list[AgentReply] = []

    async def send_completion(inbound: InboundMessage, reply: AgentReply) -> None:
        inbound
        sent.append(reply)

    manager = BackgroundTaskManager(Settings(_env_file=None), store, send_completion)
    task_id = await manager.start_command(
        _inbound(),
        context_generation=0,
        model_provider="deepseek",
        model_name="model-a",
    )
    for _ in range(10):
        if sent:
            break
        await asyncio.sleep(0.01)

    async with aiosqlite.connect(store.db_path) as conn:
        cursor = await conn.execute("SELECT status, result_text FROM background_tasks WHERE task_id = ?", (task_id,))
        row = await cursor.fetchone()

    assert row[0] == "completed"
    assert "Pythonic 命令" in row[1]
    assert sent
    assert f"之前启动的后台任务结果（{task_id}）" in sent[0].text
