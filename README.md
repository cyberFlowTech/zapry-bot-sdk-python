# Zapry Bot SDK

轻量级 Python SDK，用于在 Zapry 平台构建 Bot。

基于 `python-telegram-bot`，自动处理 Zapry 与 Telegram API 的兼容性差异，
让开发者专注于业务逻辑。

## 特性

- **Zapry 兼容层** — 自动修复 User/Chat/Update 数据格式差异
- **双平台支持** — Telegram 和 Zapry 平台一键切换
- **Handler 装饰器** — 简洁的命令、回调、消息注册方式
- **模块化注册** — `HandlerRegistry` 支持分模块管理 Handler
- **灵活配置** — 从 `.env` 或代码直接构造配置
- **Webhook + Polling** — 两种运行模式开箱即用

## 快速开始

### 安装

```bash
pip install -e /path/to/zapry-bot-sdk-python
```

### 最小示例

```python
from zapry_bot_sdk import ZapryBot, BotConfig

config = BotConfig.from_env()
bot = ZapryBot(config)

@bot.command("start")
async def start(update, context):
    await update.message.reply_text("Hello from Zapry Bot!")

@bot.command("help")
async def help_cmd(update, context):
    await update.message.reply_text("Available commands: /start, /help")

bot.run()
```

### 使用 HandlerRegistry（分模块）

```python
# handlers/tarot.py
from zapry_bot_sdk.helpers import HandlerRegistry

tarot = HandlerRegistry()

@tarot.command("tarot")
async def tarot_command(update, context):
    await update.message.reply_text("🎴 抽牌中...")

@tarot.callback("^reveal_card_")
async def reveal_card(update, context):
    ...

# main.py
from zapry_bot_sdk import ZapryBot, BotConfig
from handlers.tarot import tarot

bot = ZapryBot(BotConfig.from_env())
bot.register(tarot)
bot.run()
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TG_PLATFORM` | 平台 (`telegram` / `zapry`) | `telegram` |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | — |
| `ZAPRY_BOT_TOKEN` | Zapry Bot Token | — |
| `ZAPRY_API_BASE_URL` | Zapry API 地址 | `https://openapi.mimo.immo/bot` |
| `RUNTIME_MODE` | 运行模式 (`webhook` / `polling`) | `webhook` |
| `TELEGRAM_WEBHOOK_URL` | Telegram Webhook URL | — |
| `ZAPRY_WEBHOOK_URL` | Zapry Webhook URL | — |
| `WEBHOOK_PATH` | Webhook 路径 | — |
| `WEBAPP_HOST` | 监听地址 | `0.0.0.0` |
| `WEBAPP_PORT` | 监听端口 | `8443` |
| `DEBUG` | 调试模式 | `false` |

## 项目结构

```
zapry-bot-sdk/
├── pyproject.toml
├── README.md
├── zapry_bot_sdk/
│   ├── __init__.py          # 包入口，导出 ZapryBot, BotConfig 等
│   ├── core/
│   │   ├── __init__.py
│   │   ├── bot.py           # ZapryBot 主类
│   │   └── config.py        # BotConfig 配置
│   ├── helpers/
│   │   ├── __init__.py
│   │   └── handler_registry.py  # Handler 注册装饰器 & Registry
│   └── utils/
│       ├── __init__.py
│       ├── telegram_compat.py   # Zapry 兼容层（Monkey Patch）
│       └── logger.py            # 日志工具
└── tests/
    └── test_compat.py
```

## Zapry 兼容性

SDK 自动处理以下 Zapry 与 Telegram API 的差异：

| 问题 | 描述 | 状态 |
|------|------|------|
| #1 | User.first_name 缺失 | ✅ Zapry 已修复（SDK 保留兜底） |
| #2 | User.is_bot 缺失 | ✅ Zapry 已修复（SDK 保留兜底） |
| #3 | ID 字段为字符串 | 🔧 SDK 自动转换 |
| #4 | User.username 缺失 | 🔧 SDK 兼容处理 |
| #5 | 私聊 chat.id 错误 | ✅ Zapry 已修复 |
| #6 | chat.type 缺失 | ✅ Zapry 已修复（SDK 保留兜底） |
| #7 | 群聊 ID 带 g_ 前缀 | 🔧 SDK 自动去除 |
| #8 | 命令 entities 缺失 | ✅ Zapry 已修复（SDK 保留兜底） |
| #9 | sendChatAction 不支持 | ⚠️ 业务层需跳过 |
| #10 | editMessageText 不支持 | ⚠️ 业务层需跳过 |
| #11 | answerCallbackQuery 需 chat_id | 🔧 SDK 自动容错 |
| #14 | 不支持 Markdown | 🔧 `ZapryCompat.clean_markdown()` |

## License

MIT
