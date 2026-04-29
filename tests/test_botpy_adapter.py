from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_service.adapters.botpy_adapter import BotpyAdapter
from agent_service.config.settings import Settings
from agent_service.types import AgentReply, InboundMessage


async def _noop(message: InboundMessage) -> None:
    message


def _message(**kwargs):
    return SimpleNamespace(**kwargs)


def test_guild_at_message_maps_to_inbound_with_reply_route() -> None:
    message = _message(
        author=_message(id="user_1"),
        guild_id="guild_1",
        channel_id="channel_1",
        id="msg_1",
        event_id="event_1",
        content="<@!12345> hello",
    )

    inbound = BotpyAdapter.inbound_from_guild_message(message)

    assert inbound.channel == "botpy"
    assert inbound.conversation_id == "botpy:guild:guild_1:channel_1:user_1"
    assert inbound.sender_id == "user_1"
    assert inbound.text == "hello"
    assert inbound.raw["botpy_reply"] == {
        "kind": "guild",
        "guild_id": "guild_1",
        "channel_id": "channel_1",
        "msg_id": "msg_1",
    }


def test_group_and_c2c_messages_map_to_distinct_conversations() -> None:
    group = BotpyAdapter.inbound_from_group_message(
        _message(
            author=_message(member_openid="member_1"),
            group_openid="group_1",
            id="group_msg_1",
            event_id="event_1",
            content="群里 hello",
        )
    )
    c2c = BotpyAdapter.inbound_from_c2c_message(
        _message(
            author=_message(user_openid="openid_1"),
            id="c2c_msg_1",
            event_id="event_2",
            content="私聊 hello",
        )
    )

    assert group.conversation_id == "botpy:group:group_1:member_1"
    assert group.raw["botpy_reply"]["kind"] == "group"
    assert c2c.conversation_id == "botpy:c2c:openid_1"
    assert c2c.raw["botpy_reply"]["kind"] == "c2c"


@pytest.mark.asyncio
async def test_send_reply_uses_matching_botpy_api() -> None:
    calls = []

    class FakeAPI:
        async def post_message(self, **kwargs):
            calls.append(("guild", kwargs))

        async def post_dms(self, **kwargs):
            calls.append(("direct", kwargs))

        async def post_group_message(self, **kwargs):
            calls.append(("group", kwargs))

        async def post_c2c_message(self, **kwargs):
            calls.append(("c2c", kwargs))

    adapter = BotpyAdapter(Settings(_env_file=None), _noop)
    adapter._client = SimpleNamespace(api=FakeAPI())  # type: ignore[assignment]

    await adapter.send_reply(
        InboundMessage(
            channel="botpy",
            conversation_id="c1",
            sender_id="u1",
            message_id="m1",
            text="hello",
            raw={"botpy_reply": {"kind": "group", "group_openid": "g1", "msg_id": "m1"}},
        ),
        AgentReply(text="reply"),
    )

    assert calls == [
        (
            "group",
            {
                "group_openid": "g1",
                "msg_type": 0,
                "content": "reply",
                "msg_id": "m1",
            },
        )
    ]
