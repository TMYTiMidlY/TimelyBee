from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request

from agent_service.config.settings import Settings
from agent_service.types import AgentReply, InboundMessage

logger = logging.getLogger(__name__)


class OpeniLinkAdapter:
    name = "openilink"

    def __init__(
        self,
        settings: Settings,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.on_message = on_message
        self._http_client = http_client
        self._owns_http_client = http_client is None
        self._seen_event_ids: set[str] = set()
        self._seen_message_ids: set[str] = set()
        self._seen_lock = asyncio.Lock()
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Agent Service OpeniLink Webhook")
        webhook_path = self._normalise_path(self.settings.openilink_webhook_path)

        @app.post(webhook_path)
        async def webhook(request: Request) -> dict[str, Any]:
            return await self.handle_request(request)

        @app.get("/healthz")
        async def healthz() -> dict[str, bool]:
            return {"ok": True}

        return app

    async def listen(self) -> None:
        config = uvicorn.Config(
            self.app,
            host=self.settings.agent_service_host,
            port=self.settings.agent_service_port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        finally:
            if self._owns_http_client and self._http_client is not None:
                await self._http_client.aclose()

    async def handle_request(self, request: Request) -> dict[str, Any]:
        body = await request.body()
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid json") from exc

        if payload.get("type") == "url_verification":
            return {"challenge": str(payload.get("challenge", ""))}

        if not self.verify_signature(
            body=body,
            timestamp=request.headers.get("X-Timestamp", ""),
            signature=request.headers.get("X-Signature", ""),
            secret=self.settings.openilink_webhook_secret,
        ):
            raise HTTPException(status_code=401, detail="invalid signature")

        if payload.get("type") != "event":
            return {"ignored": True}

        event = payload.get("event") or {}
        if event.get("type") != "message.text":
            return {"ignored": True}

        inbound = self.inbound_from_envelope(payload)
        if not await self._mark_seen(inbound):
            return {"reply_async": True}

        await self.on_message(inbound)
        return {"reply_async": True}

    @staticmethod
    def verify_signature(body: bytes, timestamp: str, signature: str, secret: str) -> bool:
        if not secret or not timestamp or not signature.startswith("sha256="):
            return False
        signed_payload = timestamp.encode("utf-8") + b":" + body
        digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(f"sha256={digest}", signature)

    @staticmethod
    def inbound_from_envelope(envelope: dict[str, Any]) -> InboundMessage:
        event = envelope.get("event") or {}
        data = event.get("data") or {}
        bot = envelope.get("bot") or {}
        sender = data.get("sender") or {}
        group = data.get("group") or None

        bot_id = str(bot.get("id") or "")
        sender_id = str(sender.get("id") or "")
        group_id = str(group.get("id")) if isinstance(group, dict) and group.get("id") else ""
        event_id = str(event.get("id") or "")
        message_id = str(data.get("message_id") or event_id)
        content = str(data.get("content") or "")

        if not bot_id or not sender_id or not message_id:
            raise HTTPException(status_code=400, detail="missing required message fields")

        if group_id:
            conversation_id = f"openilink:{bot_id}:{group_id}:{sender_id}"
        else:
            conversation_id = f"openilink:{bot_id}:{sender_id}"

        return InboundMessage(
            channel="openilink",
            conversation_id=conversation_id,
            sender_id=sender_id,
            message_id=message_id,
            text=content,
            raw=envelope,
        )

    async def _mark_seen(self, inbound: InboundMessage) -> bool:
        event = inbound.raw.get("event") or {}
        event_id = str(event.get("id") or "")
        async with self._seen_lock:
            if event_id and event_id in self._seen_event_ids:
                return False
            if inbound.message_id in self._seen_message_ids:
                return False
            if event_id:
                self._seen_event_ids.add(event_id)
            self._seen_message_ids.add(inbound.message_id)
        return True

    async def send_reply(self, inbound: InboundMessage, reply: AgentReply) -> None:
        token = self.settings.openilink_app_token
        if not token:
            raise RuntimeError("OPENILINK_APP_TOKEN is required to send OpeniLink replies")

        client = self._http_client
        if client is None:
            client = httpx.AsyncClient(timeout=30)
            self._http_client = client

        trace_id = str(inbound.raw.get("trace_id") or "")
        payload = {
            "type": "text",
            "content": reply.text,
            "to": inbound.sender_id,
            "trace_id": trace_id,
        }
        url = f"{self.settings.openilink_hub_url.rstrip('/')}/bot/v1/message/send"
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"OpeniLink send failed: {response.status_code} {response.text}")
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = {}
        if data.get("ok") is False:
            raise RuntimeError(f"OpeniLink send failed: {data.get('error', 'unknown error')}")

    @staticmethod
    def _normalise_path(path: str) -> str:
        if not path:
            return "/openilink/webhook"
        return path if path.startswith("/") else f"/{path}"
