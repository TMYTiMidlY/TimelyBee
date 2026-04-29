from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agent_service.codex import CodexSessionManager
from agent_service.config.settings import Settings
from agent_service.storage.sqlite_store import SQLiteStore
from agent_service.types import AgentReply, InboundMessage

CompletionSender = Callable[[InboundMessage, AgentReply], Awaitable[None]]


@dataclass(slots=True)
class BackgroundTaskManager:
    settings: Settings
    store: SQLiteStore
    send_completion: CompletionSender
    codex: CodexSessionManager | None = None
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False)

    async def start_command(
        self,
        inbound: InboundMessage,
        *,
        context_generation: int,
        model_provider: str,
        model_name: str,
    ) -> str:
        task_id = await self.store.create_background_task(
            inbound.channel,
            inbound.conversation_id,
            description=f"command:{inbound.text}",
            context_generation=context_generation,
            model_provider=model_provider,
            model_name=model_name,
        )
        self._tasks[task_id] = asyncio.create_task(
            self._run_and_report(task_id, inbound, self._run_command(task_id, inbound)),
            name=f"agent-bg-command-{task_id}",
        )
        return task_id

    async def start_codex(
        self,
        inbound: InboundMessage,
        *,
        context_generation: int,
        model_provider: str,
        model_name: str,
    ) -> str:
        task_id = await self.store.create_background_task(
            inbound.channel,
            inbound.conversation_id,
            description=f"codex:{inbound.text}",
            context_generation=context_generation,
            model_provider=model_provider,
            model_name=model_name,
        )
        self._tasks[task_id] = asyncio.create_task(
            self._run_and_report(task_id, inbound, self._run_codex(task_id, inbound)),
            name=f"agent-bg-codex-{task_id}",
        )
        return task_id

    async def cancel(self, channel: str, conversation_id: str, task_id: str | None = None) -> int:
        running = await self.store.list_running_background_tasks(channel, conversation_id, task_id)
        affected = await self.store.cancel_background_tasks(channel, conversation_id, task_id)
        for row in running:
            task = self._tasks.get(row["task_id"])
            if task is not None:
                task.cancel()
        return affected

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def _run_command(self, task_id: str, inbound: InboundMessage) -> str:
        task_id
        inbound
        return "当前 command runner 已建立后台任务框架，但还没有匹配到已注册的 Pythonic 命令。"

    async def _run_codex(self, task_id: str, inbound: InboundMessage) -> str:
        task_id
        if self.codex is None:
            return "Codex backend 尚未初始化。"
        return await self.codex.run_once(inbound, inbound.text)

    async def _run_and_report(self, task_id: str, inbound: InboundMessage, work: Awaitable[str]) -> None:
        try:
            result = await work
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = f"后台任务失败：{exc}"
            await self.store.fail_background_task(task_id, result)
        else:
            await self.store.complete_background_task(task_id, result)
        finally:
            self._tasks.pop(task_id, None)

        await self.send_completion(
            inbound,
            AgentReply(
                text=f"这是之前启动的后台任务结果（{task_id}）：\n{result}",
                metadata={
                    "background_task_id": task_id,
                    "background_result": True,
                },
            ),
        )
