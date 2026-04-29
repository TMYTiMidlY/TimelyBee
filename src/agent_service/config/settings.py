from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    agent_provider: str = Field(default="deepseek", alias="AGENT_PROVIDER")
    agent_model: str = Field(default="", alias="AGENT_MODEL")
    intent_agent_enabled: bool = Field(default=True, alias="INTENT_AGENT_ENABLED")
    intent_agent_provider: str = Field(default="", alias="INTENT_AGENT_PROVIDER")
    intent_agent_model: str = Field(default="", alias="INTENT_AGENT_MODEL")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    minimax_api_key: str = Field(default="", alias="MINIMAX_API_KEY")
    minimax_cn_api_key: str = Field(default="", alias="MINIMAX_CN_API_KEY")
    minimax_base_url: str = Field(default="https://api.minimaxi.com/v1", alias="MINIMAX_BASE_URL")

    botpy_appid: str = Field(default="", alias="BOTPY_APPID")
    botpy_secret: str = Field(default="", alias="BOTPY_SECRET")

    codex_model: str = Field(default="gpt-5.4", alias="CODEX_MODEL")
    codex_workspace: Path = Field(default=Path("."), alias="CODEX_WORKSPACE")
    codex_bin: str = Field(default="", alias="CODEX_BIN")

    enabled_channels: str = Field(default="openilink", alias="ENABLED_CHANNELS")
    sqlite_path: Path = Field(default=Path("./data/agent.db"), alias="SQLITE_PATH")
    agent_service_host: str = Field(default="127.0.0.1", alias="AGENT_SERVICE_HOST")
    agent_service_port: int = Field(default=8080, alias="AGENT_SERVICE_PORT")

    openilink_hub_url: str = Field(default="http://localhost:9800", alias="OPENILINK_HUB_URL")
    openilink_app_token: str = Field(default="", alias="OPENILINK_APP_TOKEN")
    openilink_webhook_secret: str = Field(default="", alias="OPENILINK_WEBHOOK_SECRET")
    openilink_webhook_path: str = Field(default="/openilink/webhook", alias="OPENILINK_WEBHOOK_PATH")
    openilink_sync_reply: bool = Field(default=False, alias="OPENILINK_SYNC_REPLY")

    weixin_x_bin: str = Field(default="x-cmd", alias="WEIXIN_X_BIN")
    weixin_poll_timeout_ms: int = Field(default=3000, alias="WEIXIN_POLL_TIMEOUT_MS")
    self_sender_ids: str = Field(default="", alias="SELF_SENDER_IDS")

    @property
    def enabled_channel_list(self) -> list[str]:
        return [item.strip() for item in self.enabled_channels.split(",") if item.strip()]

    @property
    def self_sender_id_set(self) -> set[str]:
        return {item.strip() for item in self.self_sender_ids.split(",") if item.strip()}

    @property
    def effective_minimax_api_key(self) -> str:
        return self.minimax_api_key or self.minimax_cn_api_key

    @property
    def effective_model(self) -> str:
        if self.agent_model:
            return self.agent_model
        if self.agent_provider.lower() == "minimax":
            return "MiniMax-M2.7"
        return "deepseek-chat"

    @property
    def effective_intent_provider(self) -> str:
        return self.intent_agent_provider or self.agent_provider

    @property
    def effective_intent_model(self) -> str:
        if self.intent_agent_model:
            return self.intent_agent_model
        return self.effective_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
