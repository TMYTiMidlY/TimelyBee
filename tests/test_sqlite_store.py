from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from agent_service.types import AgentReply
from agent_service.storage.sqlite_store import SQLiteStore
from agent_service.types import InboundMessage


@pytest.mark.asyncio
async def test_insert_inbound_message_dedup(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    await store.init()
    msg = InboundMessage(
        channel="weixin",
        conversation_id="c1",
        sender_id="u1",
        message_id="m1",
        text="hello",
    )
    assert await store.insert_inbound_message(msg) is True
    assert await store.insert_inbound_message(msg) is False


@pytest.mark.asyncio
async def test_agent_history_round_trip_uses_pydantic_model_messages(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    await store.init()
    inbound = InboundMessage(
        channel="weixin",
        conversation_id="c1",
        sender_id="u1",
        message_id="m1",
        text="hello",
    )
    result = await Agent(model=TestModel(), output_type=str).run("hello")
    reply = AgentReply(
        text=result.output,
        model_messages_json=result.all_messages_json().decode("utf-8"),
    )

    await store.save_agent_reply(inbound, reply)

    history = await store.load_history("weixin", "c1")
    assert history == result.all_messages()


@pytest.mark.asyncio
async def test_empty_control_reply_does_not_replace_reusable_history(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    await store.init()
    inbound = InboundMessage(
        channel="weixin",
        conversation_id="c1",
        sender_id="u1",
        message_id="m1",
        text="hello",
    )
    result = await Agent(model=TestModel(), output_type=str).run("hello")
    await store.save_agent_reply(
        inbound,
        AgentReply(
            text=result.output,
            model_messages_json=result.all_messages_json().decode("utf-8"),
        ),
    )
    await store.save_agent_reply(
        InboundMessage(
            channel="weixin",
            conversation_id="c1",
            sender_id="u1",
            message_id="m2",
            text="当前模型是什么",
        ),
        AgentReply(text="当前会话模型：deepseek/deepseek-chat", model_messages_json="[]"),
    )

    history = await store.load_history("weixin", "c1")
    assert history == result.all_messages()
