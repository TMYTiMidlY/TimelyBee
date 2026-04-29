# TimelyBee

Pydantic AI 驱动的多消息渠道 Agent 框架，支持微信/QQ 接入、会话控制、日历长期记忆、后台任务和 Codex 子系统。

单进程 Python 底座：通过 OpeniLink Hub Webhook 接入微信，把消息归一后交给 Pydantic AI Agent，再用 OpeniLink Bot API 异步回发。`x-cmd weixin` 与 `botpy` 仍保留为 legacy/debug 适配器。

## 当前进度（2026-04-29）

- [x] `uv` 项目初始化与可运行 CLI（`agent-service serve` / `agent-service run` / `agent-service doctor`）
- [x] 三层架构骨架：`adapters` / `agent` / `storage`
- [x] 内部统一消息类型：`InboundMessage`、`AgentReply`、`ChannelAdapter`
- [x] SQLite 持久化：渠道消息审计/去重 + Pydantic AI `ModelMessage` 会话历史 round-trip
- [x] MiniMax OpenAI-compatible 接入（默认 `https://api.minimaxi.com/v1`）
- [x] MiniMax 实网烟雾测试（模型返回正常）
- [x] OpeniLink Hub Webhook 异步通道：签名验证、URL verification、`message.text` 归一化、去重、Bot API 回发
- [x] weixin 子进程链路本地模拟闭环（legacy debug）
- [ ] botpy 事件处理（`on_at_message_create` 等）完整实现（当前为占位适配器）

## 本次联调结果

### 1) MiniMax 实网调用（minimaxi.com）

本地读取 Hermes 配置中的 MiniMax key 后，使用：

- `AGENT_PROVIDER=minimax`
- `MINIMAX_BASE_URL=https://api.minimaxi.com/v1`
- `AGENT_MODEL=MiniMax-M2.7`

进行真实调用，返回成功（样例输出：`测试通过`）。

### 2) OpeniLink Webhook 异步链路

当前默认通道为 `openilink`。OpeniLink Hub 负责微信连接、重连、Bot 状态、消息追踪和多 Bot 管理；本项目只保留 Agent、SQLite 历史、provider 选择和 Webhook App。

Webhook 行为：

- `POST /openilink/webhook`
- `type=url_verification` 直接返回 `{"challenge": "..."}`
- `event.type=message.text` 校验 `X-Signature` 后归一化为 `InboundMessage`
- 3 秒窗口内返回 `{"reply_async": true}`
- 后台 worker 调用 Pydantic AI Agent
- 使用 `POST {OPENILINK_HUB_URL}/bot/v1/message/send` 异步发送文本回复

当前不部署 OpenClaw；OpenClaw 只作为协议参考。`x-cmd weixin` 仍可用于手工对照和排障，不再作为主通道。

## 配置项

支持环境变量或 `.env`：

- `AGENT_PROVIDER` (`deepseek` / `minimax`)
- `AGENT_MODEL`
- `DEEPSEEK_API_KEY`
- `MINIMAX_API_KEY`
- `MINIMAX_CN_API_KEY`
- `MINIMAX_BASE_URL`（默认 `https://api.minimaxi.com/v1`）
- `BOTPY_APPID`
- `BOTPY_SECRET`
- `ENABLED_CHANNELS`（默认 `openilink`，legacy 可用 `weixin` / `botpy`）
- `SQLITE_PATH`
- `AGENT_SERVICE_HOST`（默认 `127.0.0.1`）
- `AGENT_SERVICE_PORT`（默认 `8080`）
- `OPENILINK_HUB_URL`（默认 `http://localhost:9800`）
- `OPENILINK_APP_TOKEN`
- `OPENILINK_WEBHOOK_SECRET`
- `OPENILINK_WEBHOOK_PATH`（默认 `/openilink/webhook`）
- `OPENILINK_SYNC_REPLY`（默认 `false`，当前主流程固定使用 async handoff）
- `WEIXIN_X_BIN`（默认 `x-cmd`）
- `WEIXIN_POLL_TIMEOUT_MS`（weixin service log 轮询间隔，默认 `3000`）
- `SELF_SENDER_IDS`

## 快速开始

```bash
uv sync
cp .env.example .env
uv run agent-service doctor
uv run agent-service serve --channels openilink
```

本机默认双服务：

- OpeniLink Hub: `http://localhost:9800`
- Agent service: `http://localhost:8080`

在 OpeniLink Hub 中创建或安装本地 App 时，推荐配置：

- Events: `message.text`
- Scopes: `message:read`, `message:write`, `bot:read`
- Webhook URL: `http://host.docker.internal:8080/openilink/webhook`，或填写 Hub 容器/主机可访问的 Agent service 地址

异步回包使用：

```http
POST /bot/v1/message/send
Authorization: Bearer ${OPENILINK_APP_TOKEN}

{"type":"text","content":"...","to":"wxid_...","trace_id":"tr_..."}
```

## 设计审阅（对照 Pydantic AI 标准示例）

### 已符合

- 统一 `Agent` runtime，按 provider factory 切模型（符合 model-agnostic 思路）
- DeepSeek 使用专用 provider；MiniMax 使用 OpenAI-compatible provider
- 工具注册走 `@agent.tool_plain`（与官方工具调用风格一致）
- 多渠道适配器与 Agent 解耦，便于后续扩展
- OpeniLink 通道只处理 Hub App 协议，不承担微信连接和重连状态管理

### 已补齐

- 会话历史使用 Pydantic AI `ModelMessagesTypeAdapter` / `all_messages_json()` 存取
- `message_history` 传入 `ModelMessage` 序列，保留 tool call/return 等结构
- 渠道原始消息仍单独保存，用于审计、去重和排障
- OpeniLink `trace_id`、`installation_id`、`bot.id`、`event.id`、`group.id` 保存在 `InboundMessage.raw`
- 会话 key：
  - 私聊：`openilink:{bot_id}:{sender_id}`
  - 群聊：`openilink:{bot_id}:{group_id}:{sender_id}`

### 待完善（下一步）

- [ ] 群消息定向策略实测
  - 现在是什么：群消息的 `group.id` 已保存在 `InboundMessage.raw`；v1 回发 body 使用 `to=sender_id`，遵循 OpeniLink 默认语义。
  - 为什么要做：不同 Hub/Bot provider 对群内回复目标可能存在差异，可能需要后续补充 `to/group` 策略。
  - 要补什么：在真实微信群中验证 Bot API 的投递行为，必要时扩展 OpeniLink reply payload。
  - 验收标准：私聊和群聊都能稳定回到触发消息的上下文。

- [ ] botpy 真实事件接入
  - 现在是什么：`BotpyAdapter` 仍是占位实现，只会保持进程存活，不会真的连接 QQ 频道/机器人事件流。
  - 为什么要做：启用 `ENABLED_CHANNELS=botpy` 后，服务应该能接收 QQ 机器人事件，例如 `on_at_message_create`，把用户消息归一化成 `InboundMessage`，再把 Agent 回复发回对应频道。
  - 要补什么：初始化 botpy client、注册消息事件回调、解析 guild/channel/user/message id、实现发送文本回复、处理 bot 自己发出的消息避免自循环。
  - 验收标准：配置 `BOTPY_APPID` / `BOTPY_SECRET` 后，真实 QQ 频道里 at 机器人，数据库能看到 inbound 消息，Agent 能回复到同一个频道。

- [ ] 长回复分片
  - 现在是什么：Agent 的回复会作为一整段文本交给渠道发送。
  - 为什么要做：微信、QQ 等渠道通常有单条消息长度限制；模型偶尔会生成很长的回答，一整段发送可能失败、被截断，或者体验很差。
  - 要补什么：给 `AgentReply.text` 增加按渠道的安全切分逻辑，例如按最大字符数、段落边界、代码块边界分片；每片按顺序发送，并在任一片失败时停止并记录错误。
  - 验收标准：构造超长回复时，weixin/botpy 能收到多条顺序正确的消息；数据库能记录完整回复和分片发送结果。

- [ ] weixin 定向回复与真实 schema 完整适配
  - 现在是什么：当前 weixin 适配器已能通过 `x-cmd weixin listen poll` 收到文本，并用 `x-cmd weixin send --text <reply>` 发出消息；但发送命令没有携带更精细的会话目标参数。
  - 为什么要做：真实多会话环境里，机器人可能同时收到来自不同联系人/群/上下文的消息。只发送 `--text` 可能依赖 x-cmd 当前默认会话，不能保证回复一定回到触发消息的那个会话。
  - 要补什么：用真实 `x-cmd weixin` 消息样本确认字段，例如 `conversation_id`、`chat_id`、`from_user_id`、`context_token` 或 OpenClaw schema 中的上下文标识；把这些字段保存到 `InboundMessage.raw`，发送时按 x-cmd 支持的参数定向回复。
  - 验收标准：从两个不同联系人/群同时发消息，Agent 回复能各自回到正确会话；重启服务后仍能根据持久化字段继续正确归属历史。

- [ ] listener 异常退避与后台服务化
  - 现在是什么：listener 失败后固定 sleep 再重试，项目内提供了 `scripts/start_weixin_agent.sh` / `scripts/stop_weixin_agent.sh` 方便后台运行。
  - 为什么要做：真实运行时可能遇到网络抖动、x-cmd gateway 重启、凭据过期、长轮询卡住等情况，固定重试不够稳。
  - 要补什么：增加指数退避、最大退避时间、连续失败计数、关键状态日志；条件允许时补 systemd user service 或其他正式进程管理配置。
  - 验收标准：手动停止/重启 x-cmd gateway 或断网恢复后，agent 能自动恢复监听；日志能看出失败原因和恢复时间。

## 参考文档

- Pydantic AI Agent: https://ai.pydantic.dev/agent/
- Pydantic AI OpenAI-compatible: https://ai.pydantic.dev/models/openai/
- Pydantic AI Message History: https://ai.pydantic.dev/message-history
- DeepSeek API Docs: https://api-docs.deepseek.com/
- MiniMax OpenAI API Compatible (global): https://platform.minimax.io/docs/api-reference/text-openai-api
- MiniMax OpenAI API Compatible (CN / minimaxi.com): https://platform.minimax.com/docs/api-reference/text-openai-api
- botpy: https://github.com/tencent-connect/botpy
- x-cmd weixin listen: https://cn.x-cmd.com/mod/weixin/listen/
- x-cmd weixin send: https://cn.x-cmd.com/mod/weixin/send/
- OpeniLink Hub: https://github.com/openilink/openilink-hub
- OpeniLink App Development: https://raw.githubusercontent.com/openilink/openilink-hub/main/docs/app-development.md
- OpenClaw Weixin schema: https://github.com/Tencent/openclaw-weixin
