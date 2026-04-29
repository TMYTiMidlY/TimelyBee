from __future__ import annotations

import aiosqlite
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

from agent_service.assistant.intent import IntentDecision, classify_by_rules
from agent_service.assistant.router import AssistantRouter
from agent_service.config.settings import Settings
from agent_service.storage.sqlite_store import SQLiteStore
from agent_service.types import InboundMessage


async def _normal_response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=f"normal:{len(messages)}")])


def _inbound(text: str, message_id: str = "m1", conversation_id: str = "c1") -> InboundMessage:
    return InboundMessage(
        channel="openilink",
        conversation_id=conversation_id,
        sender_id="u1",
        message_id=message_id,
        text=text,
    )


def _router() -> AssistantRouter:
    settings = Settings(_env_file=None, INTENT_AGENT_ENABLED=False, DEEPSEEK_API_KEY="dummy")
    agent = Agent(model=FunctionModel(_normal_response), system_prompt="test")
    return AssistantRouter(settings, agent, "test")


def test_rule_classifier_extracts_control_intents() -> None:
    assert classify_by_rules("清空上下文").intent == "new_clear"
    assert classify_by_rules("停止后台任务").intent == "cancel_stop_kill"
    assert classify_by_rules("switch model deepseek-reasoner").target_model == "deepseek-reasoner"
    assert classify_by_rules("当前模型是什么").intent == "show_model"
    assert classify_by_rules("明天提醒我交报告").intent == "calendar"
    assert classify_by_rules("进入 codex 处理这个项目").intent == "codex"


@pytest.mark.asyncio
async def test_clear_only_resets_short_context_not_model_tasks_or_calendar(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    await store.init()
    router = _router()

    await store.set_session_model("openilink", "c1", provider="deepseek", model="deepseek-reasoner")
    task_id = await store.create_background_task(
        "openilink",
        "c1",
        description="background work",
        context_generation=0,
        model_provider="deepseek",
        model_name="deepseek-reasoner",
    )
    await store.create_calendar_event(
        "openilink",
        "c1",
        user_id="u1",
        title="明天提醒我交报告",
        raw_text="明天提醒我交报告",
    )

    reply = await router.route(_inbound("清空上下文"), IntentDecision(intent="new_clear", confidence=1), store)
    state = await store.get_session_state("openilink", "c1")
    events = await store.list_calendar_events("openilink", "c1")

    async with aiosqlite.connect(store.db_path) as conn:
        cursor = await conn.execute("SELECT status, model_name FROM background_tasks WHERE task_id = ?", (task_id,))
        task = await cursor.fetchone()

    assert "短期上下文已清空" in reply.text
    assert state.short_context_generation == 1
    assert state.model_name == "deepseek-reasoner"
    assert events[0]["title"] == "明天提醒我交报告"
    assert task == ("running", "deepseek-reasoner")


@pytest.mark.asyncio
async def test_switch_model_is_conversation_scoped_and_resettable(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    await store.init()
    router = _router()

    await router.route(
        _inbound("switch model deepseek-reasoner", conversation_id="c1"),
        IntentDecision(intent="switch_model", confidence=1, target_model="deepseek-reasoner"),
        store,
    )

    c1 = await store.get_session_state("openilink", "c1")
    c2 = await store.get_session_state("openilink", "c2")
    assert c1.model_name == "deepseek-reasoner"
    assert c2.model_name is None

    show = await router.route(_inbound("当前模型", conversation_id="c1"), IntentDecision(intent="show_model", confidence=1), store)
    assert "deepseek-reasoner" in show.text

    await router.route(_inbound("恢复默认模型", conversation_id="c1"), IntentDecision(intent="reset_model", confidence=1), store)
    c1 = await store.get_session_state("openilink", "c1")
    assert c1.model_name is None


@pytest.mark.asyncio
async def test_background_task_keeps_model_snapshot_after_later_switch(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    await store.init()
    router = _router()

    await store.set_session_model("openilink", "c1", provider="deepseek", model="model-a")
    reply = await router.route(
        _inbound("后台执行一次数据整理"),
        IntentDecision(intent="command", confidence=1, background=True),
        store,
    )
    await store.set_session_model("openilink", "c1", provider="deepseek", model="model-b")

    task_id = reply.metadata["task_id"]
    async with aiosqlite.connect(store.db_path) as conn:
        cursor = await conn.execute("SELECT model_name, status FROM background_tasks WHERE task_id = ?", (task_id,))
        task = await cursor.fetchone()

    assert task == ("model-a", "running")


@pytest.mark.asyncio
async def test_calendar_survives_context_clear(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    await store.init()
    router = _router()

    await router.route(_inbound("明天提醒我交报告"), IntentDecision(intent="calendar", confidence=1), store)
    await router.route(_inbound("清空上下文", message_id="m2"), IntentDecision(intent="new_clear", confidence=1), store)
    reply = await router.route(_inbound("查询安排", message_id="m3"), IntentDecision(intent="calendar", confidence=1), store)

    assert "明天提醒我交报告" in reply.text


@pytest.mark.asyncio
async def test_codex_mode_routes_plain_followup_to_codex_backend(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "agent.db")
    await store.init()
    router = _router()

    class FakeCodex:
        async def run_foreground(self, inbound, state, prompt):
            assert state.codex_mode is True
            return f"codex:{prompt}"

    router.codex = FakeCodex()  # type: ignore[assignment]
    await store.set_codex_mode("openilink", "c1", enabled=True, thread_id="thr_1")

    reply = await router.route(
        _inbound("继续实现测试", message_id="m2"),
        IntentDecision(intent="normal_chat", confidence=1),
        store,
    )

    assert reply.text == "codex:继续实现测试"
