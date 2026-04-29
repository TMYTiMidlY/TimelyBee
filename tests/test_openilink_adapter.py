from __future__ import annotations

import json
import time

import httpx
import pytest

from agent_service.adapters.openilink import OpeniLinkAdapter
from agent_service.config.settings import Settings
from agent_service.types import InboundMessage


async def _noop(message: InboundMessage) -> None:
    message


def _recorder(calls: list[InboundMessage]):
    async def record(message: InboundMessage) -> None:
        calls.append(message)

    return record


def _envelope(
    *,
    event_id: str = "evt_1",
    message_id: str | int = "msg_1",
    group_id: str | None = None,
    content: str = "hello",
) -> dict:
    return {
        "v": 1,
        "type": "event",
        "trace_id": "tr_abc",
        "installation_id": "inst_1",
        "bot": {"id": "bot_1"},
        "event": {
            "type": "message.text",
            "id": event_id,
            "timestamp": 1711234567,
            "data": {
                "message_id": message_id,
                "sender": {"id": "wxid_1", "role": "user"},
                "group": {"id": group_id} if group_id else None,
                "content": content,
                "msg_type": "text",
                "items": [],
            },
        },
    }


def _signed_body(payload: dict, secret: str = "secret") -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = OpeniLinkAdapter.verify_signature
    assert not signature(body=body, timestamp=timestamp, signature="sha256=bad", secret=secret)
    import hashlib
    import hmac

    digest = hmac.new(secret.encode("utf-8"), f"{timestamp}:".encode("utf-8") + body, hashlib.sha256).hexdigest()
    return body, {"X-Timestamp": timestamp, "X-Signature": f"sha256={digest}", "Content-Type": "application/json"}


async def _post(
    adapter: OpeniLinkAdapter,
    payload: dict,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=adapter.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        if headers is None:
            return await client.post(adapter.settings.openilink_webhook_path, json=payload)
        body, signed_headers = _signed_body(payload)
        signed_headers.update(headers)
        return await client.post(adapter.settings.openilink_webhook_path, content=body, headers=signed_headers)


@pytest.mark.asyncio
async def test_url_verification_returns_challenge_without_side_effects() -> None:
    calls: list[InboundMessage] = []
    adapter = OpeniLinkAdapter(Settings(OPENILINK_WEBHOOK_SECRET="secret"), _recorder(calls))

    response = await _post(adapter, {"v": 1, "type": "url_verification", "challenge": "random_string"})

    assert response.status_code == 200
    assert response.json() == {"challenge": "random_string"}
    assert calls == []


@pytest.mark.asyncio
async def test_hmac_signature_accepts_valid_request_and_rejects_invalid_request() -> None:
    calls: list[InboundMessage] = []
    adapter = OpeniLinkAdapter(Settings(OPENILINK_WEBHOOK_SECRET="secret"), _recorder(calls))

    valid_body, valid_headers = _signed_body(_envelope(), secret="secret")
    transport = httpx.ASGITransport(app=adapter.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        valid = await client.post("/openilink/webhook", content=valid_body, headers=valid_headers)
        invalid = await client.post(
            "/openilink/webhook",
            content=valid_body,
            headers={**valid_headers, "X-Signature": "sha256=bad"},
        )

    assert valid.status_code == 200
    assert valid.json() == {"reply_async": True}
    assert invalid.status_code == 401
    assert len(calls) == 1


def test_message_text_maps_to_direct_conversation_key() -> None:
    inbound = OpeniLinkAdapter.inbound_from_envelope(_envelope())

    assert inbound.channel == "openilink"
    assert inbound.conversation_id == "openilink:bot_1:wxid_1"
    assert inbound.sender_id == "wxid_1"
    assert inbound.message_id == "msg_1"
    assert inbound.text == "hello"
    assert inbound.raw["trace_id"] == "tr_abc"


def test_message_text_maps_to_group_conversation_key() -> None:
    inbound = OpeniLinkAdapter.inbound_from_envelope(_envelope(group_id="group_1"))

    assert inbound.conversation_id == "openilink:bot_1:group_1:wxid_1"
    assert inbound.raw["event"]["data"]["group"]["id"] == "group_1"


@pytest.mark.asyncio
async def test_duplicate_event_id_or_message_id_is_not_enqueued_twice() -> None:
    calls: list[InboundMessage] = []
    adapter = OpeniLinkAdapter(Settings(OPENILINK_WEBHOOK_SECRET="secret"), _recorder(calls))

    await _post(adapter, _envelope(event_id="evt_1", message_id="msg_1"), headers={})
    await _post(adapter, _envelope(event_id="evt_1", message_id="msg_2"), headers={})
    await _post(adapter, _envelope(event_id="evt_2", message_id="msg_1"), headers={})

    assert [call.message_id for call in calls] == ["msg_1"]


@pytest.mark.asyncio
async def test_async_webhook_returns_quickly() -> None:
    async def slow_enqueue(message: InboundMessage) -> None:
        import asyncio

        await asyncio.sleep(0.01)
        message

    adapter = OpeniLinkAdapter(Settings(OPENILINK_WEBHOOK_SECRET="secret"), slow_enqueue)

    started = time.monotonic()
    response = await _post(adapter, _envelope(), headers={})
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert response.json() == {"reply_async": True}
    assert elapsed < 3
