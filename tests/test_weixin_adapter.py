from __future__ import annotations

from agent_service.adapters.weixin import WeixinAdapter
from agent_service.config.settings import Settings


async def _noop(message) -> None:
    message


def test_parse_x_cmd_received_message_log_line() -> None:
    adapter = WeixinAdapter(Settings(), _noop)

    inbound = adapter._parse_line("- I|weixin: Received a message: pydantic ai weixin real test 003")

    assert inbound is not None
    assert inbound.text == "pydantic ai weixin real test 003"
    assert inbound.channel == "weixin"


def test_parse_ignores_x_cmd_status_log_line() -> None:
    adapter = WeixinAdapter(Settings(), _noop)

    assert adapter._parse_line("- I|weixin: Starting long-poll listener...") is None


def test_parse_real_x_cmd_weixin_schema() -> None:
    adapter = WeixinAdapter(Settings(), _noop)

    inbound = adapter._parse_line(
        """[{"message_id":7455138545079806856,"from_user_id":"u1@im.wechat","to_user_id":"bot@im.bot","client_id":"client-1","session_id":"","group_id":"","message_type":1,"item_list":[{"type":1,"text_item":{"text":"完整agent测试004，请简短回复收到"}}],"context_token":"ctx-1"}]"""
    )

    assert inbound is not None
    assert inbound.message_id == "7455138545079806856"
    assert inbound.sender_id == "u1@im.wechat"
    assert inbound.conversation_id == "u1@im.wechat"
    assert inbound.text == "完整agent测试004，请简短回复收到"
    assert inbound.raw["context_token"] == "ctx-1"


def test_parse_multiple_service_log_messages() -> None:
    adapter = WeixinAdapter(Settings(), _noop)

    messages = adapter._parse_messages(
        """[{"message_id":1,"from_user_id":"u1","item_list":[{"text_item":{"text":"one"}}]},{"message_id":2,"from_user_id":"u2","item_list":[{"text_item":{"text":"two"}}]}]"""
    )

    assert [message.message_id for message in messages] == ["1", "2"]
    assert [message.text for message in messages] == ["one", "two"]


def test_parse_multiline_service_log_messages() -> None:
    adapter = WeixinAdapter(Settings(), _noop)

    messages = adapter._parse_messages(
        "\n".join(
            [
                """[{"message_id":1,"from_user_id":"u1","item_list":[{"text_item":{"text":"one"}}]}]""",
                """[{"message_id":2,"from_user_id":"u2","item_list":[{"text_item":{"text":"two"}}]}]""",
            ]
        )
    )

    assert [message.message_id for message in messages] == ["1", "2"]
    assert [message.text for message in messages] == ["one", "two"]
