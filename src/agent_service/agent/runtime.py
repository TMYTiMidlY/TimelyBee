from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from agent_service.agent.model_factory import build_model
from agent_service.assistant.intent import IntentAgent
from agent_service.assistant.router import AssistantRouter
from agent_service.background import BackgroundTaskManager
from agent_service.codex import CodexSessionManager
from agent_service.config.settings import Settings
from agent_service.storage.sqlite_store import SQLiteStore
from agent_service.types import AgentReply, InboundMessage


SYSTEM_PROMPT = (
    "你是一个多渠道机器人助手。"
    "回复时保持简洁，优先处理用户问题。"
)


@dataclass(slots=True)
class AgentRuntime:
    settings: Settings
    agent: Agent[dict, str] = field(init=False)
    intent_agent: IntentAgent = field(init=False)
    router: AssistantRouter = field(init=False)

    def __post_init__(self) -> None:
        model = build_model(self.settings)
        self.agent = Agent(model=model, system_prompt=SYSTEM_PROMPT)
        self.intent_agent = IntentAgent(self.settings)
        self.router = AssistantRouter(self.settings, self.agent, SYSTEM_PROMPT)

        @self.agent.tool_plain
        def echo_tool(text: str) -> str:
            return text

    def attach_services(
        self,
        *,
        background_tasks: BackgroundTaskManager | None = None,
        codex: CodexSessionManager | None = None,
    ) -> None:
        self.router.background_tasks = background_tasks
        self.router.codex = codex

    async def handle(
        self,
        inbound: InboundMessage,
        history: list[ModelMessage] | None = None,
        store: SQLiteStore | None = None,
    ) -> AgentReply:
        if store is not None:
            decision = await self.intent_agent.classify(inbound)
            return await self.router.route(inbound, decision, store)

        history = history or []
        prompt = (
            f"[channel={inbound.channel}] [conversation_id={inbound.conversation_id}] "
            f"[sender_id={inbound.sender_id}] {inbound.text}"
        )
        result = await self.agent.run(prompt, message_history=history)
        return AgentReply(
            text=result.output.strip() or "（空回复）",
            metadata={"model": self.settings.effective_model, "provider": self.settings.agent_provider},
            model_messages_json=result.all_messages_json().decode("utf-8"),
        )
