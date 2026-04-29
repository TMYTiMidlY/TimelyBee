from __future__ import annotations

from agent_service.config.settings import Settings


def test_settings_default_model_by_provider() -> None:
    deepseek = Settings(AGENT_PROVIDER="deepseek")
    minimax = Settings(AGENT_PROVIDER="minimax")
    assert deepseek.effective_model == "deepseek-chat"
    assert minimax.effective_model == "MiniMax-M2.7"


def test_enabled_channels_parse() -> None:
    settings = Settings(ENABLED_CHANNELS="botpy, weixin")
    assert settings.enabled_channel_list == ["botpy", "weixin"]


def test_weixin_defaults_to_x_cmd() -> None:
    assert Settings.model_fields["weixin_x_bin"].default == "x-cmd"
