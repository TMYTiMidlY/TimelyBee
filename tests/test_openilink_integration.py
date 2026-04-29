from __future__ import annotations

import asyncio
import json
import time

import aiosqlite
import httpx
import pytest

from agent_service.adapters.openilink import OpeniLinkAdapter
from agent_service.config.settings import Settings
from agent_service.orchestrator import Orchestrator
from agent_service.types import AgentReply, InboundMessage


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[InboundMessage] = []

    async def handle(self, inbound: InboundMessage, history=None) -> AgentReply:
        self.calls.append(inbound)
        return AgentReply(text=f"reply:{inbound.text}", metadata={"fake": True}, model_messages_json="[]")


def _envelope() -> dict:
    return {
        "v": 1,
        "type": "event",
        "trace_id": "tr_integration",
        "installation_id": "inst_1",
        "bot": {"id": "bot_1"},
        "event": {
            "type": "message.text",
            "id": "evt_integration",
            "timestamp": 1711234567,
            "data": {
                "message_id": "msg_integration",
                "sender": {"id": "wxid_1", "role": "user"},
                "group": None,
                "content": "ping",
                "msg_type": "text",
                "items": [],
            },
        },
    }


def _signed_body(payload: dict, secret: str) -> tuple[bytes, dict[str, str]]:
    import hashlib
    import hmac

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    digest = hmac.new(secret.encode("utf-8"), f"{timestamp}:".encode("utf-8") + body, hashlib.sha256).hexdigest()
    return body, {"X-Timestamp": timestamp, "X-Signature": f"sha256={digest}", "Content-Type": "application/json"}


@pytest.mark.asyncio
async def test_openilink_webhook_runs_agent_persists_and_sends_reply(tmp_path) -> None:
    sent_payloads: list[dict] = []

    def bot_api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://hub.test/bot/v1/message/send"
        assert request.headers["Authorization"] == "Bearer tok_1"
        sent_payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"ok": True, "client_id": "client_1", "trace_id": "tr_integration"})

    settings = Settings(
        ENABLED_CHANNELS="openilink",
        SQLITE_PATH=tmp_path / "agent.db",
        OPENILINK_HUB_URL="http://hub.test",
        OPENILINK_APP_TOKEN="tok_1",
        OPENILINK_WEBHOOK_SECRET="secret",
        DEEPSEEK_API_KEY="dummy",
    )
    orchestrator = Orchestrator(settings)
    fake_runtime = FakeRuntime()
    orchestrator.runtime = fake_runtime
    adapter = orchestrator.adapters["openilink"]
    assert isinstance(adapter, OpeniLinkAdapter)
    adapter._http_client = httpx.AsyncClient(transport=httpx.MockTransport(bot_api_handler))

    await orchestrator.store.init()
    worker = asyncio.create_task(orchestrator._agent_worker())
    try:
        body, headers = _signed_body(_envelope(), "secret")
        transport = httpx.ASGITransport(app=adapter.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/openilink/webhook", content=body, headers=headers)

        await asyncio.wait_for(orchestrator.inbound_queue.join(), timeout=3)

        assert response.status_code == 200
        assert response.json() == {"reply_async": True}
        assert [call.text for call in fake_runtime.calls] == ["ping"]
        assert sent_payloads == [
            {
                "type": "text",
                "content": "reply:ping",
                "to": "wxid_1",
                "trace_id": "tr_integration",
            }
        ]

        async with aiosqlite.connect(settings.sqlite_path) as conn:
            cursor = await conn.execute("SELECT sender_id, message_id, text FROM messages ORDER BY id")
            rows = await cursor.fetchall()
        assert rows == [
            ("wxid_1", "msg_integration", "ping"),
            ("agent", "agent:msg_integration", "reply:ping"),
        ]
    finally:
        worker.cancel()
        await adapter._http_client.aclose()
        try:
            await worker
        except asyncio.CancelledError:
            pass
