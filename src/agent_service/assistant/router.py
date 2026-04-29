from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic_ai import Agent

from agent_service.agent.model_factory import build_model
from agent_service.assistant.intent import IntentDecision
from agent_service.assistant.session import SessionState
from agent_service.background import BackgroundTaskManager
from agent_service.codex import CodexSessionManager
from agent_service.config.settings import Settings
from agent_service.storage.sqlite_store import SQLiteStore
from agent_service.types import AgentReply, InboundMessage


@dataclass(slots=True)
class AssistantRouter:
    settings: Settings
    default_agent: Agent[dict, str]
    system_prompt: str
    background_tasks: BackgroundTaskManager | None = None
    codex: CodexSessionManager | None = None

    async def route(self, inbound: InboundMessage, decision: IntentDecision, store: SQLiteStore) -> AgentReply:
        state = await store.get_session_state(inbound.channel, inbound.conversation_id)
        metadata = {"intent": decision.intent, "confidence": decision.confidence}

        if decision.intent == "new_clear":
            state = await store.clear_short_context(inbound.channel, inbound.conversation_id)
            metadata["context_generation"] = state.short_context_generation
            return AgentReply(
                text="已开启新话题，短期上下文已清空。日历、后台任务和当前模型保持不变。",
                metadata=metadata,
            )

        if decision.intent == "show_model":
            provider = state.effective_provider(self.settings.agent_provider)
            model = state.effective_model(self.settings.effective_model)
            metadata.update({"provider": provider, "model": model, "context_generation": state.short_context_generation})
            return AgentReply(text=f"当前会话模型：{provider}/{model}", metadata=metadata)

        if decision.intent == "reset_model":
            state = await store.reset_session_model(inbound.channel, inbound.conversation_id)
            metadata["context_generation"] = state.short_context_generation
            return AgentReply(
                text=f"当前会话已恢复默认模型：{self.settings.agent_provider}/{self.settings.effective_model}",
                metadata=metadata,
            )

        if decision.intent == "switch_model":
            target_model = decision.target_model or _extract_model_name(inbound.text)
            if not target_model:
                return AgentReply(text="要切换模型的话，请告诉我目标模型名。", metadata=metadata)
            state = await store.set_session_model(
                inbound.channel,
                inbound.conversation_id,
                provider=self.settings.agent_provider,
                model=target_model,
            )
            metadata.update({"model": target_model, "context_generation": state.short_context_generation})
            return AgentReply(
                text=f"已将当前会话模型切换为：{self.settings.agent_provider}/{target_model}。短期上下文不受影响。",
                metadata=metadata,
            )

        if decision.intent == "cancel_stop_kill":
            if self.background_tasks is not None:
                count = await self.background_tasks.cancel(inbound.channel, inbound.conversation_id, decision.task_id)
            else:
                count = await store.cancel_background_tasks(inbound.channel, inbound.conversation_id, decision.task_id)
            target = decision.task_id or "当前会话下全部后台任务"
            return AgentReply(text=f"已停止 {target}，影响 {count} 个任务。短期上下文保持不变。", metadata=metadata)

        if decision.intent == "calendar":
            return await self._handle_calendar(inbound, store, state, metadata)

        if decision.intent == "command":
            return await self._handle_command(inbound, decision, store, state, metadata)

        if decision.intent == "codex":
            return await self._handle_codex(inbound, decision, store, state, metadata)

        if decision.intent == "resume":
            if state.codex_mode:
                return await self._continue_codex(inbound, store, state, metadata)
            return AgentReply(text="已继续当前会话上下文。", metadata=metadata)

        if state.codex_mode:
            return await self._continue_codex(inbound, store, state, metadata)

        return await self._run_normal_chat(inbound, store, state, metadata)

    async def _run_normal_chat(
        self,
        inbound: InboundMessage,
        store: SQLiteStore,
        state: SessionState,
        metadata: dict,
    ) -> AgentReply:
        history = await store.load_history(
            inbound.channel,
            inbound.conversation_id,
            context_generation=state.short_context_generation,
        )
        provider = state.effective_provider(self.settings.agent_provider)
        model_name = state.effective_model(self.settings.effective_model)
        agent = self.default_agent
        if provider != self.settings.agent_provider or model_name != self.settings.effective_model:
            agent = Agent(
                model=build_model(self.settings, provider_name=provider, model_name=model_name),
                system_prompt=self.system_prompt,
            )

        prompt = (
            f"[channel={inbound.channel}] [conversation_id={inbound.conversation_id}] "
            f"[sender_id={inbound.sender_id}] {inbound.text}"
        )
        result = await agent.run(prompt, message_history=history)
        metadata.update(
            {
                "model": model_name,
                "provider": provider,
                "context_generation": state.short_context_generation,
            }
        )
        return AgentReply(
            text=result.output.strip() or "（空回复）",
            metadata=metadata,
            model_messages_json=result.all_messages_json().decode("utf-8"),
        )

    async def _handle_calendar(
        self,
        inbound: InboundMessage,
        store: SQLiteStore,
        state: SessionState,
        metadata: dict,
    ) -> AgentReply:
        metadata["context_generation"] = state.short_context_generation
        if any(token in inbound.text for token in ["查询", "看看", "有哪些", "列出", "查看安排"]):
            events = await store.list_calendar_events(inbound.channel, inbound.conversation_id, limit=5)
            if not events:
                return AgentReply(text="当前没有查到已记录的日历事项。", metadata=metadata)
            lines = [f"- {event['title']}" for event in events]
            return AgentReply(text="最近记录的日历事项：\n" + "\n".join(lines), metadata=metadata)

        title = inbound.text.strip()
        await store.create_calendar_event(
            inbound.channel,
            inbound.conversation_id,
            user_id=inbound.sender_id,
            title=title,
            raw_text=inbound.text,
        )
        return AgentReply(text="已记录到日历模块。clear/new 不会删除这个长期记忆。", metadata=metadata)

    async def _handle_command(
        self,
        inbound: InboundMessage,
        decision: IntentDecision,
        store: SQLiteStore,
        state: SessionState,
        metadata: dict,
    ) -> AgentReply:
        provider = state.effective_provider(self.settings.agent_provider)
        model = state.effective_model(self.settings.effective_model)
        metadata.update({"provider": provider, "model": model, "context_generation": state.short_context_generation})
        if decision.background:
            if self.background_tasks is not None:
                task_id = await self.background_tasks.start_command(
                    inbound,
                    context_generation=state.short_context_generation,
                    model_provider=provider,
                    model_name=model,
                )
            else:
                task_id = await store.create_background_task(
                    inbound.channel,
                    inbound.conversation_id,
                    description=f"command:{inbound.text}",
                    context_generation=state.short_context_generation,
                    model_provider=provider,
                    model_name=model,
                )
            return AgentReply(
                text=f"已创建后台 command 任务 {task_id}，它会固定使用当前模型 {provider}/{model}。",
                metadata={**metadata, "task_id": task_id},
            )
        return AgentReply(
            text="已识别为 command。当前只允许注册过的 Pythonic 命令执行；这条消息没有匹配到可执行命令。",
            metadata=metadata,
        )

    async def _handle_codex(
        self,
        inbound: InboundMessage,
        decision: IntentDecision,
        store: SQLiteStore,
        state: SessionState,
        metadata: dict,
    ) -> AgentReply:
        provider = state.effective_provider(self.settings.agent_provider)
        model = state.effective_model(self.settings.effective_model)
        metadata.update({"provider": provider, "model": model, "context_generation": state.short_context_generation})
        lowered = inbound.text.lower()
        if "退出" in inbound.text or "exit" in lowered:
            await store.set_codex_mode(inbound.channel, inbound.conversation_id, enabled=False)
            return AgentReply(text="已退出 Codex 前台模式。", metadata=metadata)

        if decision.background:
            if self.background_tasks is not None:
                task_id = await self.background_tasks.start_codex(
                    inbound,
                    context_generation=state.short_context_generation,
                    model_provider=provider,
                    model_name=model,
                )
            else:
                task_id = await store.create_background_task(
                    inbound.channel,
                    inbound.conversation_id,
                    description=f"codex:{inbound.text}",
                    context_generation=state.short_context_generation,
                    model_provider=provider,
                    model_name=model,
                )
            return AgentReply(
                text=f"已创建后台 Codex 任务 {task_id}。完成汇报会标明它来自之前启动的后台任务。",
                metadata={**metadata, "task_id": task_id},
            )

        if self.codex is not None:
            try:
                text = await self.codex.enter(
                    inbound,
                    state,
                    prompt=None if _is_codex_enter_only(inbound.text) else inbound.text,
                )
            except Exception as exc:
                return AgentReply(text=f"Codex 启动失败：{exc}", metadata=metadata)
            return AgentReply(text=text, metadata=metadata)

        await store.set_codex_mode(inbound.channel, inbound.conversation_id, enabled=True)
        return AgentReply(
            text="已进入 Codex 前台模式。后续代码任务会继续同一个 Codex 会话，直到你说退出 Codex。",
            metadata=metadata,
        )

    async def _continue_codex(
        self,
        inbound: InboundMessage,
        store: SQLiteStore,
        state: SessionState,
        metadata: dict,
    ) -> AgentReply:
        metadata["context_generation"] = state.short_context_generation
        if self.codex is None:
            return AgentReply(text="当前处于 Codex 前台模式，但 Codex backend 尚未初始化。", metadata=metadata)
        try:
            text = await self.codex.run_foreground(inbound, state, inbound.text)
        except Exception as exc:
            return AgentReply(text=f"Codex 执行失败：{exc}", metadata=metadata)
        state = await store.get_session_state(inbound.channel, inbound.conversation_id)
        metadata["codex_thread_id"] = state.codex_thread_id
        return AgentReply(text=text, metadata=metadata)


def _extract_model_name(text: str) -> str | None:
    match = re.search(r"([A-Za-z0-9_.:/-]+)$", text.strip())
    return match.group(1) if match else None


def _is_codex_enter_only(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered in {"codex", "enter codex", "进入 codex", "进入codex", "进入代码环境"}
