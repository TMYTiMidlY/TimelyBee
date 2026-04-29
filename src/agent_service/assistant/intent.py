from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from agent_service.agent.model_factory import build_model
from agent_service.config.settings import Settings
from agent_service.types import InboundMessage


IntentLabel = Literal[
    "new_clear",
    "resume",
    "cancel_stop_kill",
    "switch_model",
    "show_model",
    "reset_model",
    "calendar",
    "command",
    "codex",
    "normal_chat",
]


class IntentDecision(BaseModel):
    intent: IntentLabel = "normal_chat"
    confidence: float = Field(default=0.5, ge=0, le=1)
    target_model: str | None = None
    task_id: str | None = None
    background: bool = False
    reason: str = ""


INTENT_SYSTEM_PROMPT = """
你是轻量控制意图分类器，只分类，不执行任务。
必须把用户消息归入一个 intent：
new_clear, resume, cancel_stop_kill, switch_model, show_model, reset_model,
calendar, command, codex, normal_chat。
规则：
- new/clear 只表示清空短期上下文。
- cancel/stop/kill 只表示停止后台任务。
- switch/show/reset model 只管理当前会话模型。
- 日程、提醒、安排查询归 calendar。
- 简单执行任务归 command。
- 复杂代码任务、明确提到 Codex、进入/退出 Codex 归 codex。
返回结构化 JSON，不要改写用户问题。
""".strip()


class IntentAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.agent: Agent[None, IntentDecision] | None = None
        if settings.intent_agent_enabled and _has_provider_credentials(settings, settings.effective_intent_provider):
            model = build_model(
                settings,
                provider_name=settings.effective_intent_provider,
                model_name=settings.effective_intent_model,
            )
            self.agent = Agent(model=model, output_type=IntentDecision, system_prompt=INTENT_SYSTEM_PROMPT)

    async def classify(self, inbound: InboundMessage) -> IntentDecision:
        heuristic = classify_by_rules(inbound.text)
        if heuristic.confidence >= 0.85 or self.agent is None:
            return heuristic

        prompt = f"用户消息：{inbound.text}"
        try:
            result = await self.agent.run(prompt)
        except Exception:
            return heuristic
        decision = result.output
        if decision.intent == "switch_model" and not decision.target_model:
            decision.target_model = heuristic.target_model
        if heuristic.background and not decision.background:
            decision.background = True
        return decision


def classify_by_rules(text: str) -> IntentDecision:
    raw = text.strip()
    lowered = raw.lower()

    model_match = re.search(r"(?:switch model|切换模型|模型切到|改用模型|使用模型|换成)\s*[:：为到]?\s*([A-Za-z0-9_.:/-]+)", raw)
    if model_match:
        return IntentDecision(
            intent="switch_model",
            confidence=0.92,
            target_model=model_match.group(1),
            reason="explicit model switch",
        )
    if any(token in lowered for token in ["show model", "current model", "当前模型", "现在用什么模型", "用的哪个模型"]):
        return IntentDecision(intent="show_model", confidence=0.92, reason="explicit model query")
    if any(token in lowered for token in ["reset model", "恢复默认模型", "重置模型"]):
        return IntentDecision(intent="reset_model", confidence=0.92, reason="explicit model reset")

    if any(token in lowered for token in ["clear", "new chat", "new topic", "清空上下文", "开启新话题", "新话题", "清除上下文"]):
        return IntentDecision(intent="new_clear", confidence=0.92, reason="explicit context clear")
    if any(token in lowered for token in ["cancel", "stop", "kill", "停止后台", "停止任务", "取消任务", "杀掉任务"]):
        task_match = re.search(r"(task[_-]?\w+|任务\s*\w+)", raw, flags=re.IGNORECASE)
        task_id = task_match.group(1).replace(" ", "") if task_match else None
        return IntentDecision(intent="cancel_stop_kill", confidence=0.88, task_id=task_id, reason="explicit task stop")
    if any(token in lowered for token in ["resume", "继续之前", "继续刚才", "接着刚才", "继续 codex"]):
        return IntentDecision(intent="resume", confidence=0.86, reason="explicit resume")

    background = any(token in lowered for token in ["后台", "background", "异步"])
    if "codex" in lowered or any(token in raw for token in ["进入代码环境", "退出代码环境", "复杂代码任务"]):
        return IntentDecision(intent="codex", confidence=0.9, background=background, reason="codex keyword")
    if any(token in raw for token in ["日程", "提醒", "安排", "日历", "会议", "闹钟"]):
        return IntentDecision(intent="calendar", confidence=0.88, reason="calendar keyword")
    if any(token in lowered for token in ["run ", "execute", "command", "shell"]) or any(
        token in raw for token in ["执行", "运行", "跑一下", "算一下"]
    ):
        return IntentDecision(intent="command", confidence=0.78, background=background, reason="command keyword")
    return IntentDecision(intent="normal_chat", confidence=0.55, reason="default")


def _has_provider_credentials(settings: Settings, provider_name: str) -> bool:
    provider = provider_name.lower()
    if provider == "deepseek":
        return bool(settings.deepseek_api_key)
    if provider == "minimax":
        return bool(settings.effective_minimax_api_key)
    return False
