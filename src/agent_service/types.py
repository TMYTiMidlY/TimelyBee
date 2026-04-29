from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class Attachment:
    kind: str
    url: str | None = None
    name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InboundMessage:
    channel: str
    conversation_id: str
    sender_id: str
    message_id: str
    text: str
    attachments: list[Attachment] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentReply:
    text: str
    attachments: list[Attachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    model_messages_json: str | None = None


class ChannelAdapter(Protocol):
    name: str

    async def listen(self) -> None: ...

    async def send_reply(self, inbound: InboundMessage, reply: AgentReply) -> None: ...
