from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_service.assistant.session import SessionState
from agent_service.config.settings import Settings
from agent_service.storage.sqlite_store import SQLiteStore
from agent_service.types import InboundMessage


@dataclass(slots=True)
class CodexSessionManager:
    settings: Settings
    store: SQLiteStore
    _codex: Any | None = field(default=None, init=False)
    _threads: dict[tuple[str, str], Any] = field(default_factory=dict, init=False)

    async def close(self) -> None:
        if self._codex is not None:
            await self._codex.__aexit__(None, None, None)
            self._codex = None
            self._threads.clear()

    async def enter(self, inbound: InboundMessage, state: SessionState, prompt: str | None = None) -> str:
        thread = await self._get_or_start_thread(inbound, state)
        if prompt:
            return await self._run_thread(thread, prompt)
        return "已进入 Codex 前台模式。后续代码任务会继续同一个 Codex 会话，直到你说退出 Codex。"

    async def run_foreground(self, inbound: InboundMessage, state: SessionState, prompt: str) -> str:
        thread = await self._get_or_start_thread(inbound, state)
        return await self._run_thread(thread, prompt)

    async def run_once(self, inbound: InboundMessage, prompt: str) -> str:
        state = await self.store.get_session_state(inbound.channel, inbound.conversation_id)
        thread = await self._start_thread(inbound, state)
        return await self._run_thread(thread, prompt)

    async def _get_or_start_thread(self, inbound: InboundMessage, state: SessionState) -> Any:
        key = (inbound.channel, inbound.conversation_id)
        if key in self._threads:
            return self._threads[key]
        return await self._start_thread(inbound, state)

    async def _start_thread(self, inbound: InboundMessage, state: SessionState) -> Any:
        codex = await self._get_codex()
        cwd = _workspace_path(self.settings.codex_workspace)
        thread = await codex.thread_start(model=self.settings.codex_model, cwd=str(cwd))
        thread_id = str(getattr(thread, "id", "") or getattr(thread, "thread_id", ""))
        await self.store.set_codex_mode(
            inbound.channel,
            inbound.conversation_id,
            enabled=True,
            thread_id=thread_id or state.codex_thread_id,
        )
        self._threads[(inbound.channel, inbound.conversation_id)] = thread
        return thread

    async def _get_codex(self) -> Any:
        if self._codex is not None:
            return self._codex
        try:
            from codex_app_server import AppServerConfig, AsyncCodex
        except ImportError as exc:
            raise RuntimeError(
                "Codex Python SDK is not installed. Install it from the local Codex repo with "
                "`cd sdk/python && python -m pip install -e .`."
            ) from exc

        if self.settings.codex_bin:
            self._codex = AsyncCodex(AppServerConfig(codex_bin=self.settings.codex_bin))
        else:
            self._codex = AsyncCodex()
        await self._codex.__aenter__()
        return self._codex

    async def _run_thread(self, thread: Any, prompt: str) -> str:
        result = await thread.run(prompt)
        return str(getattr(result, "final_response", "") or result)


def _workspace_path(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return (Path.cwd() / expanded).resolve()
