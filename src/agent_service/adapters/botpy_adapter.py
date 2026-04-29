from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from agent_service.config.settings import Settings
from agent_service.types import AgentReply, InboundMessage

logger = logging.getLogger(__name__)


class BotpyAdapter:
    name = "botpy"

    def __init__(self, settings: Settings, on_message: Callable[[InboundMessage], Awaitable[None]]) -> None:
        self.settings = settings
        self.on_message = on_message

    async def listen(self) -> None:
        if not self.settings.botpy_appid or not self.settings.botpy_secret:
            logger.warning("BOTPY_APPID/BOTPY_SECRET missing, botpy listener skipped")
            while True:
                await asyncio.sleep(60)
        logger.info("botpy adapter scaffold is enabled but runtime integration is pending")
        while True:
            await asyncio.sleep(60)

    async def send_reply(self, inbound: InboundMessage, reply: AgentReply) -> None:
        logger.info("botpy reply placeholder: conversation=%s text=%s", inbound.conversation_id, reply.text)
