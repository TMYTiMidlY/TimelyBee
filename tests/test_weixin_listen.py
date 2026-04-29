from __future__ import annotations

import asyncio

import pytest

from agent_service.adapters.weixin import WeixinAdapter
from agent_service.config.settings import Settings


@pytest.mark.asyncio
async def test_listen_merges_x_cmd_stderr(monkeypatch) -> None:
    created = {}

    calls = 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal calls
        calls += 1
        created["stderr"] = kwargs["stderr"]

        class Proc:
            returncode = 0

            async def communicate(self):
                return b"", None

        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    adapter = WeixinAdapter(Settings(WEIXIN_POLL_TIMEOUT_MS=1), lambda message: None)

    task = asyncio.create_task(adapter.listen())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()

    assert created["stderr"] is asyncio.subprocess.STDOUT
    assert calls >= 1
