from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import botpy

from agent_service.config.settings import Settings
from agent_service.types import AgentReply, InboundMessage

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"<@!?\d+>")


class BotpyAdapter:
    name = "botpy"

    def __init__(self, settings: Settings, on_message: Callable[[InboundMessage], Awaitable[None]]) -> None:
        self.settings = settings
        self.on_message = on_message
        self._client: botpy.Client | None = None

    async def listen(self) -> None:
        if not self.settings.botpy_appid or not self.settings.botpy_secret:
            logger.warning("BOTPY_APPID/BOTPY_SECRET missing, botpy listener skipped")
            while True:
                await asyncio.sleep(60)

        intents = botpy.Intents(
            public_guild_messages=True,
            direct_message=True,
            public_messages=True,
        )
        client = _RelayBotpyClient(intents=intents, on_message=self.on_message)
        self._client = client
        async with client:
            await client.start(appid=self.settings.botpy_appid, secret=self.settings.botpy_secret)

    async def send_reply(self, inbound: InboundMessage, reply: AgentReply) -> None:
        if self._client is None:
            raise RuntimeError("botpy client is not running")

        route = inbound.raw.get("botpy_reply") or {}
        kind = route.get("kind")
        if kind == "guild":
            await self._client.api.post_message(
                channel_id=route["channel_id"],
                content=reply.text,
                msg_id=route.get("msg_id"),
            )
            return
        if kind == "direct":
            await self._client.api.post_dms(
                guild_id=route["guild_id"],
                content=reply.text,
                msg_id=route.get("msg_id"),
            )
            return
        if kind == "group":
            await self._client.api.post_group_message(
                group_openid=route["group_openid"],
                msg_type=0,
                content=reply.text,
                msg_id=route.get("msg_id"),
            )
            return
        if kind == "c2c":
            await self._client.api.post_c2c_message(
                openid=route["openid"],
                msg_type=0,
                content=reply.text,
                msg_id=route.get("msg_id"),
            )
            return
        raise RuntimeError(f"unsupported botpy reply route: {kind!r}")

    @staticmethod
    def inbound_from_guild_message(message: Any) -> InboundMessage:
        author_id = str(getattr(message.author, "id", "") or "unknown")
        guild_id = str(getattr(message, "guild_id", "") or "")
        channel_id = str(getattr(message, "channel_id", "") or "")
        message_id = str(getattr(message, "id", "") or getattr(message, "event_id", "") or "")
        text = _clean_content(str(getattr(message, "content", "") or ""))
        return InboundMessage(
            channel="botpy",
            conversation_id=f"botpy:guild:{guild_id}:{channel_id}:{author_id}",
            sender_id=author_id,
            message_id=message_id,
            text=text,
            raw={
                "botpy_kind": "guild",
                "botpy_message": _to_plain(message),
                "botpy_reply": {
                    "kind": "guild",
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                    "msg_id": message_id,
                },
            },
        )

    @staticmethod
    def inbound_from_direct_message(message: Any) -> InboundMessage:
        author_id = str(getattr(message.author, "id", "") or "unknown")
        guild_id = str(getattr(message, "guild_id", "") or "")
        message_id = str(getattr(message, "id", "") or getattr(message, "event_id", "") or "")
        return InboundMessage(
            channel="botpy",
            conversation_id=f"botpy:direct:{guild_id}:{author_id}",
            sender_id=author_id,
            message_id=message_id,
            text=_clean_content(str(getattr(message, "content", "") or "")),
            raw={
                "botpy_kind": "direct",
                "botpy_message": _to_plain(message),
                "botpy_reply": {
                    "kind": "direct",
                    "guild_id": guild_id,
                    "msg_id": message_id,
                },
            },
        )

    @staticmethod
    def inbound_from_group_message(message: Any) -> InboundMessage:
        group_openid = str(getattr(message, "group_openid", "") or "")
        member_openid = str(getattr(message.author, "member_openid", "") or "unknown")
        message_id = str(getattr(message, "id", "") or getattr(message, "event_id", "") or "")
        return InboundMessage(
            channel="botpy",
            conversation_id=f"botpy:group:{group_openid}:{member_openid}",
            sender_id=member_openid,
            message_id=message_id,
            text=_clean_content(str(getattr(message, "content", "") or "")),
            raw={
                "botpy_kind": "group",
                "botpy_message": _to_plain(message),
                "botpy_reply": {
                    "kind": "group",
                    "group_openid": group_openid,
                    "msg_id": message_id,
                },
            },
        )

    @staticmethod
    def inbound_from_c2c_message(message: Any) -> InboundMessage:
        openid = str(getattr(message.author, "user_openid", "") or "unknown")
        message_id = str(getattr(message, "id", "") or getattr(message, "event_id", "") or "")
        return InboundMessage(
            channel="botpy",
            conversation_id=f"botpy:c2c:{openid}",
            sender_id=openid,
            message_id=message_id,
            text=_clean_content(str(getattr(message, "content", "") or "")),
            raw={
                "botpy_kind": "c2c",
                "botpy_message": _to_plain(message),
                "botpy_reply": {
                    "kind": "c2c",
                    "openid": openid,
                    "msg_id": message_id,
                },
            },
        )


class _RelayBotpyClient(botpy.Client):
    def __init__(self, *, intents: botpy.Intents, on_message: Callable[[InboundMessage], Awaitable[None]]) -> None:
        super().__init__(intents=intents)
        self._on_message = on_message

    async def on_ready(self) -> None:
        logger.info("botpy robot is ready: %s", getattr(self.robot, "name", "unknown"))

    async def on_at_message_create(self, message: Any) -> None:
        await self._on_message(BotpyAdapter.inbound_from_guild_message(message))

    async def on_direct_message_create(self, message: Any) -> None:
        await self._on_message(BotpyAdapter.inbound_from_direct_message(message))

    async def on_group_at_message_create(self, message: Any) -> None:
        await self._on_message(BotpyAdapter.inbound_from_group_message(message))

    async def on_c2c_message_create(self, message: Any) -> None:
        await self._on_message(BotpyAdapter.inbound_from_c2c_message(message))


def _clean_content(content: str) -> str:
    return _MENTION_RE.sub("", content).strip()


def _to_plain(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [_to_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return {key: _to_plain(item) for key, item in vars(value).items() if not key.startswith("_")}
    slots: list[str] = []
    for cls in type(value).mro():
        raw_slots = getattr(cls, "__slots__", ())
        if isinstance(raw_slots, str):
            slots.append(raw_slots)
        else:
            slots.extend(raw_slots)
    if slots:
        return {
            key: _to_plain(getattr(value, key))
            for key in slots
            if not key.startswith("_") and hasattr(value, key)
        }
    return repr(value)
