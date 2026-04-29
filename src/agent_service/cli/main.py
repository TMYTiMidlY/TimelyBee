from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path

from agent_service.config.settings import get_settings
from agent_service.orchestrator import Orchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-service")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="run multi-channel agent orchestrator")
    run_parser.add_argument("--channels", default="", help="override ENABLED_CHANNELS")
    sub.add_parser("doctor", help="check runtime dependencies and configuration")
    return parser


def doctor() -> int:
    settings = get_settings()
    print("== agent-service doctor ==")
    print(f"provider={settings.agent_provider}, model={settings.effective_model}")
    print(f"enabled_channels={settings.enabled_channel_list}")
    print(f"sqlite_path={settings.sqlite_path}")

    x_bin = shutil.which(settings.weixin_x_bin)
    if "weixin" in settings.enabled_channel_list:
        if x_bin:
            print(f"[ok] x cli found: {x_bin}")
        else:
            print(f"[warn] x cli not found: {settings.weixin_x_bin}")

    if "botpy" in settings.enabled_channel_list:
        botpy_ok = bool(settings.botpy_appid and settings.botpy_secret)
        print("[ok] botpy credentials set" if botpy_ok else "[warn] BOTPY_APPID/BOTPY_SECRET missing")

    parent = Path(settings.sqlite_path).parent
    if not parent.exists():
        print(f"[info] sqlite parent will be created: {parent}")
    return 0


async def run(channels_override: str = "") -> None:
    settings = get_settings()
    if channels_override:
        settings.enabled_channels = channels_override
    orchestrator = Orchestrator(settings)
    await orchestrator.run()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "doctor":
        raise SystemExit(doctor())
    if args.command == "run":
        asyncio.run(run(args.channels))
