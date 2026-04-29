from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable

from agent_service.config.settings import Settings
from agent_service.types import AgentReply, InboundMessage

logger = logging.getLogger(__name__)

_RECEIVED_MESSAGE_MARKER = "Received a message:"


class WeixinAdapter:
    name = "weixin"

    def __init__(
        self,
        settings: Settings,
        on_message: Callable[[InboundMessage], Awaitable[None]],
    ) -> None:
        self.settings = settings
        self.on_message = on_message

    async def listen(self) -> None:
        while True:
            try:
                await self._ensure_gateway_service()
                proc = await asyncio.create_subprocess_exec(
                    self.settings.weixin_x_bin,
                    "weixin",
                    "bot",
                    "service",
                    "log",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await proc.communicate()
                output = stdout.decode("utf-8", errors="replace").strip()
                if proc.returncode == 0 and output:
                    for inbound in self._parse_messages(output):
                        await self.on_message(inbound)
                elif proc.returncode != 0:
                    logger.error("weixin service log failed: %s", output)
                await asyncio.sleep(max(self.settings.weixin_poll_timeout_ms / 1000, 1.0))
            except Exception:
                logger.exception("weixin listener failed, retrying")
                await asyncio.sleep(2.0)

    async def _ensure_gateway_service(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            self.settings.weixin_x_bin,
            "weixin",
            "bot",
            "service",
            "start",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await proc.communicate()

    def _parse_messages(self, output: str) -> list[InboundMessage]:
        messages: list[InboundMessage] = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                inbound = self._parse_line(line)
                if inbound is not None:
                    messages.append(inbound)
                continue
            if isinstance(payload, list):
                messages.extend(message for item in payload if (message := self._parse_payload(item, line)) is not None)
            else:
                message = self._parse_payload(payload, line)
                if message is not None:
                    messages.append(message)
        if messages:
            return messages
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            inbound = self._parse_line(output)
            return [inbound] if inbound is not None else []
        if isinstance(payload, list):
            messages = [self._parse_payload(item, output) for item in payload]
            return [message for message in messages if message is not None]
        message = self._parse_payload(payload, output)
        return [message] if message is not None else []

    def _parse_line(self, line: str) -> InboundMessage | None:
        if not line:
            return None
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            if _RECEIVED_MESSAGE_MARKER in line:
                text = line.split(_RECEIVED_MESSAGE_MARKER, maxsplit=1)[1].strip()
                payload = {"raw_line": line, "text": text}
            elif line.startswith("- ") and "|weixin:" in line:
                return None
            else:
                payload = {"raw_line": line, "text": line}
        return self._parse_payload(payload, line)

    def _parse_payload(self, payload: object, raw_line: str) -> InboundMessage | None:
        if isinstance(payload, list):
            if not payload:
                return None
            payload = payload[0]
        if not isinstance(payload, dict):
            payload = {"raw_line": raw_line, "text": str(payload)}
        text = self._extract_text(payload)
        if not text:
            return None
        fallback_message_id = hashlib.sha256(raw_line.encode("utf-8")).hexdigest()
        message_id = str(payload.get("message_id") or payload.get("id") or fallback_message_id)
        sender_id = str(payload.get("from_user_id") or payload.get("sender_id") or "unknown")
        conversation_id = str(
            payload.get("conversation_id")
            or payload.get("chat_id")
            or payload.get("group_id")
            or payload.get("session_id")
            or sender_id
        )
        return InboundMessage(
            channel="weixin",
            conversation_id=conversation_id,
            sender_id=sender_id,
            message_id=message_id,
            text=text,
            raw=payload,
        )

    def _extract_text(self, payload: dict) -> str:
        direct_text = payload.get("text") or payload.get("content")
        if direct_text:
            return str(direct_text)
        for item in payload.get("item_list") or []:
            text_item = item.get("text_item") or {}
            text = text_item.get("text")
            if text:
                return str(text)
        return str(payload.get("raw_line") or "")

    async def send_reply(self, inbound: InboundMessage, reply: AgentReply) -> None:
        proc = await asyncio.create_subprocess_exec(
            self.settings.weixin_x_bin,
            "weixin",
            "send",
            "--text",
            reply.text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")
            logger.error("weixin send failed: %s", detail)
            raise RuntimeError(f"weixin send failed: {detail}")
        elif stdout:
            logger.debug("weixin send output: %s", stdout.decode("utf-8", errors="replace"))
