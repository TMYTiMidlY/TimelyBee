from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from agent_service.adapters.openilink import OpeniLinkAdapter
from agent_service.adapters.botpy_adapter import BotpyAdapter
from agent_service.adapters.weixin import WeixinAdapter
from agent_service.agent.runtime import AgentRuntime
from agent_service.background import BackgroundTaskManager
from agent_service.codex import CodexSessionManager
from agent_service.config.settings import Settings
from agent_service.storage.sqlite_store import SQLiteStore
from agent_service.types import ChannelAdapter, InboundMessage

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = SQLiteStore(settings.sqlite_path)
        self.runtime = AgentRuntime(settings)
        self.codex = CodexSessionManager(settings, self.store)
        self.background_tasks = BackgroundTaskManager(settings, self.store, self._send_background_reply, self.codex)
        self.runtime.attach_services(background_tasks=self.background_tasks, codex=self.codex)
        self.inbound_queue: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=1024)
        self.adapters: dict[str, ChannelAdapter] = {}
        self._build_adapters()

    def _build_adapters(self) -> None:
        if "openilink" in self.settings.enabled_channel_list:
            self.adapters["openilink"] = OpeniLinkAdapter(self.settings, self.enqueue_inbound)
        if "weixin" in self.settings.enabled_channel_list:
            self.adapters["weixin"] = WeixinAdapter(self.settings, self.enqueue_inbound)
        if "botpy" in self.settings.enabled_channel_list:
            self.adapters["botpy"] = BotpyAdapter(self.settings, self.enqueue_inbound)

    async def enqueue_inbound(self, inbound: InboundMessage) -> None:
        await self.inbound_queue.put(inbound)

    async def run(self) -> None:
        await self.store.init()
        if not self.adapters:
            raise RuntimeError("No channel adapter enabled. Check ENABLED_CHANNELS.")

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._agent_worker())
            for adapter in self.adapters.values():
                tg.create_task(adapter.listen())

    async def _agent_worker(self) -> None:
        while True:
            inbound = await self.inbound_queue.get()
            try:
                if inbound.sender_id in self.settings.self_sender_id_set:
                    continue
                inserted = await self.store.insert_inbound_message(inbound)
                if not inserted:
                    continue
                reply = await self.runtime.handle(inbound, store=self.store)
                await self.adapters[inbound.channel].send_reply(inbound, reply)
                await self.store.save_agent_reply(inbound, reply)
            except Exception:
                logger.exception("agent worker failed for %s/%s", inbound.channel, inbound.message_id)
            finally:
                self.inbound_queue.task_done()

    async def _send_background_reply(self, inbound: InboundMessage, reply) -> None:
        await self.adapters[inbound.channel].send_reply(inbound, reply)
        task_id = str(reply.metadata.get("background_task_id", "task"))
        await self.store.save_agent_reply(inbound=replace(inbound, message_id=f"{inbound.message_id}:bg:{task_id}"), reply=reply)
