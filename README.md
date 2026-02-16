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
- **主动触发调度器** — `ProactiveScheduler` 定时触发主动消息，支持自定义触发器
- **反馈检测框架** — `FeedbackDetector` 自动检测用户反馈信号，调整回复风格
- **偏好注入工具** — `build_preference_prompt()` 将偏好转为 AI system prompt
- **Middleware 管道** — 洋葱模型中间件，支持 before/after、拦截、上下文传递
- **Tool Calling 框架** — `@tool` 装饰器自动生成 JSON schema，`ToolRegistry` 统一管理
- **OpenAI 适配器** — `OpenAIToolAdapter` 一键对接 OpenAI function calling
- **Memory 持久化** — 三层记忆模型（工作/短期/长期），可插拔存储，自动提取，Zapry 云端预留
- **Agent Loop** — ReAct 自动推理循环，LLM 自主调用工具直到产出最终回答
- **Guardrails 安全护栏** — Input/Output 护栏 + Tripwire 机制，防 prompt injection/内容泄露
- **Tracing 结构化追踪** — agent/llm/tool/guardrail Span 层级追踪，可导出到 OpenTelemetry

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

## 主动触发 & 自我反思

### ProactiveScheduler — 主动消息调度器

让 Bot 主动关心用户，定时检查触发条件并发送消息。

```python
from zapry_bot_sdk import ProactiveScheduler

# 创建调度器（60 秒轮询一次）
scheduler = ProactiveScheduler(
    interval=60,
    send_fn=my_send_message,  # async def send(user_id, text)
)

# 方式 1：装饰器注册触发器
@scheduler.trigger("daily_greeting")
async def check_greeting(ctx):
    if ctx.now.hour == 12 and ctx.now.minute <= 30:
        return ["user_001", "user_002"]  # 需要发送的用户
    return []

@check_greeting.message
async def greeting_msg(ctx, user_id):
    return f"中午好~ 今天状态怎么样？"

# 方式 2：编程式注册
scheduler.add_trigger("birthday", check_fn, message_fn)

# 生命周期
await scheduler.start()   # 启动后台轮询
await scheduler.stop()    # 停止

# 用户级开关
await scheduler.enable_user("user_001")
await scheduler.disable_user("user_001")
```

### FeedbackDetector — 反馈检测 & 偏好调整

从用户消息中检测反馈信号（如"太长了"→简洁风格），自动调整偏好。

```python
from zapry_bot_sdk import FeedbackDetector, build_preference_prompt

detector = FeedbackDetector()

# 检测反馈信号
result = detector.detect("太长了，说重点")
# result.matched => True
# result.changes => {"style": "concise"}

# 一步完成检测 + 更新偏好
prefs = {"style": "balanced"}
await detector.detect_and_adapt("user_001", "太长了", prefs)
# prefs => {"style": "concise", "updated_at": "..."}

# 自定义关键词（默认中文，可覆盖）
detector.add_pattern("language", "english", ["speak english", "in english"])

# 偏好注入 AI prompt
prompt = build_preference_prompt({"style": "concise", "tone": "casual"})
# => "回复风格偏好：\n这位用户偏好简洁的回复..."
```

### 与 ZapryBot 集成

```python
bot = ZapryBot(config)
scheduler = ProactiveScheduler(interval=60)
detector = FeedbackDetector()

@bot.on_post_init
async def post_init(app):
    scheduler.send_fn = lambda uid, text: app.bot.send_message(int(uid), text)
    await scheduler.start()

@bot.on_post_shutdown
async def shutdown(app):
    await scheduler.stop()

@bot.message()
async def on_message(update, context):
    user_id = str(update.effective_user.id)
    # 在回复后异步检测反馈
    result = await detector.detect_and_adapt(user_id, update.message.text, user_prefs)
```

## Middleware 管道

洋葱模型中间件，每个 middleware 包裹下一层，可在 handler 前后执行逻辑。

```python
from zapry_bot_sdk import ZapryBot, BotConfig

bot = ZapryBot(BotConfig.from_env())

# 注册中间件（按顺序包裹）
async def timer_middleware(ctx, next_fn):
    import time
    start = time.time()
    await next_fn()  # 调用下一层
    print(f"耗时: {time.time() - start:.3f}s")

async def auth_middleware(ctx, next_fn):
    if not is_authorized(ctx.update):
        return  # 不调用 next_fn → 拦截
    ctx.extra["role"] = "admin"
    await next_fn()

bot.use(timer_middleware)
bot.use(auth_middleware)
```

执行顺序: `timer before → auth before → handler → auth after → timer after`

## Tool Calling 框架

LLM-agnostic 的工具注册、schema 管理与调用分发。

```python
from zapry_bot_sdk.tools import tool, ToolRegistry

# @tool 装饰器自动从 type hints + docstring 生成 JSON schema
@tool
async def get_weather(city: str, unit: str = "celsius") -> str:
    """获取指定城市的当前天气。

    Args:
        city: 城市名称
        unit: 温度单位
    """
    return f"{city}: 25°C, 晴"

registry = ToolRegistry()
registry.register(get_weather)

# 导出 schema
schema = registry.to_json_schema()
openai_tools = registry.to_openai_schema()

# 执行工具
result = await registry.execute("get_weather", {"city": "上海"})
```

### OpenAI Function Calling 适配器

```python
from zapry_bot_sdk.tools.openai_adapter import OpenAIToolAdapter

adapter = OpenAIToolAdapter(registry)

# 1. 获取 tools 参数
tools_param = adapter.to_openai_tools()

# 2. 调用 OpenAI
response = await client.chat.completions.create(
    model="gpt-4o", messages=messages, tools=tools_param,
)

# 3. 处理 tool_calls
if response.choices[0].message.tool_calls:
    results = await adapter.handle_tool_calls(
        response.choices[0].message.tool_calls
    )
    messages.extend(adapter.results_to_messages(results))
```

## Memory 持久化框架

三层记忆模型，按 `{agent_id}:{user_id}` 隔离，可插拔存储后端。

```python
from zapry_bot_sdk.memory import MemorySession, InMemoryStore, SQLiteMemoryStore

# 创建 session（agent+user 隔离）
session = MemorySession(
    agent_id="my_agent",
    user_id="user_123",
    store=SQLiteMemoryStore("memory.db"),  # 或 InMemoryStore()
)

# 加载所有记忆
ctx = await session.load()
# ctx.short_term  → 对话历史
# ctx.long_term   → 用户档案
# ctx.working     → 会话临时数据

# 添加消息（自动持久化 + 缓冲区管理）
await session.add_message("user", "我今年25岁，在上海做程序员")
await session.add_message("assistant", "了解了~")

# 获取可注入 LLM 的 prompt
prompt = session.format_for_prompt()

# 自动记忆提取（需设置 extractor）
from zapry_bot_sdk.memory import LLMMemoryExtractor
session.extractor = LLMMemoryExtractor(llm_fn=my_llm_call)
await session.extract_if_needed()

# 手动更新长期记忆
await session.update_long_term({"basic_info": {"age": 25}})

# 清空
await session.clear_history()  # 只清对话
await session.clear_all()      # 清除所有
```

### 存储后端

| 后端 | 用途 | 持久化 |
|------|------|--------|
| `InMemoryStore` | 开发/测试 | 否 |
| `SQLiteMemoryStore` | 本地生产 | 是 |
| `ZapryCloudStore` | Zapry 云端托管（预留） | 是 |

### 与 Middleware 集成

```python
async def memory_middleware(ctx, next_fn):
    session = MemorySession("bot", get_user_id(ctx.update), store)
    await session.load()
    ctx.extra["session"] = session
    await next_fn()
    await session.extract_if_needed()

bot.use(memory_middleware)
```

## Agent Loop（自动推理循环）

ReAct 模式：LLM 自主决策调用工具、获取结果、再决策，直到产出最终回答。

```python
from zapry_bot_sdk.agent import AgentLoop
from zapry_bot_sdk.tools import ToolRegistry, tool

@tool
async def get_weather(city: str) -> str:
    """获取天气。"""
    return f"{city}: 25°C"

registry = ToolRegistry()
registry.register(get_weather)

async def my_llm(messages, tools=None):
    response = await openai_client.chat.completions.create(
        model="gpt-4o", messages=messages, tools=tools,
    )
    return response.choices[0].message

loop = AgentLoop(
    llm_fn=my_llm,
    tool_registry=registry,
    system_prompt="You are a helpful assistant.",
    max_turns=10,  # 防止无限循环
)

result = await loop.run("上海天气怎么样？")
print(result.final_output)       # "上海现在 25°C，晴天。"
print(result.tool_calls_count)   # 1
print(result.total_turns)        # 2 (1次工具调用 + 1次最终回答)
print(result.stopped_reason)     # "completed"
```

### 事件钩子（可观测性）

```python
from zapry_bot_sdk.agent import AgentHooks

hooks = AgentHooks(
    on_llm_start=lambda turn, msgs: print(f"Turn {turn}: calling LLM..."),
    on_tool_start=lambda name, args: print(f"Calling tool: {name}"),
    on_tool_end=lambda name, result, err: print(f"Tool result: {result}"),
    on_error=lambda e: print(f"Error: {e}"),
)
loop = AgentLoop(llm_fn=my_llm, tool_registry=registry, hooks=hooks)
```

### 与 Memory 集成

```python
session = MemorySession("my_agent", "user_123", store)
ctx = await session.load()

result = await loop.run(
    "记住我的生日是10月15日",
    conversation_history=await session.short_term.get_history_dicts(),
    extra_context=session.format_for_prompt(),
)
```

## 项目结构

```
zapry-bot-sdk/
├── pyproject.toml
├── README.md
├── zapry_bot_sdk/
│   ├── __init__.py          # 包入口
│   ├── core/
│   │   ├── bot.py           # ZapryBot 主类（含 middleware 集成）
│   │   ├── config.py        # BotConfig 配置
│   │   └── middleware.py    # Middleware 洋葱管道
│   ├── helpers/
│   │   └── handler_registry.py  # Handler 注册装饰器 & Registry
│   ├── proactive/
│   │   ├── scheduler.py     # ProactiveScheduler 主动消息调度器
│   │   └── feedback.py      # FeedbackDetector 反馈检测 & 偏好注入
│   ├── tools/
│   │   ├── registry.py      # @tool 装饰器 + ToolRegistry + schema 生成
│   │   └── openai_adapter.py # OpenAI function calling 适配器
│   ├── agent/
│   │   └── loop.py          # AgentLoop ReAct 推理循环（含 Guardrails + Tracing）
│   ├── guardrails/
│   │   └── engine.py        # Guardrails 安全护栏 + Tripwire 机制
│   ├── tracing/
│   │   └── engine.py        # 结构化 Span 追踪系统
│   ├── memory/
│   │   ├── session.py       # MemorySession 便捷 API
│   │   ├── store.py         # MemoryStore Protocol + InMemoryStore
│   │   ├── store_sqlite.py  # SQLiteMemoryStore
│   │   ├── short_term.py    # ShortTermMemory 对话历史
│   │   ├── long_term.py     # LongTermMemory 用户档案
│   │   ├── working.py       # WorkingMemory 会话临时数据
│   │   ├── buffer.py        # ConversationBuffer 对话缓冲
│   │   ├── extractor.py     # MemoryExtractor + LLMMemoryExtractor
│   │   ├── formatter.py     # prompt 注入格式化
│   │   └── types.py         # 数据类型定义
│   └── utils/
│       ├── telegram_compat.py   # Zapry 兼容层
│       └── logger.py            # 日志工具
└── tests/
    ├── test_compat.py       # 兼容层测试
    ├── test_proactive.py    # 主动触发 & 反馈检测测试（44 项）
    ├── test_middleware.py   # Middleware 管道测试（9 项）
    ├── test_tools.py        # Tool Calling + OpenAI 适配器测试（32 项）
    ├── test_memory.py       # Memory 框架全量测试（55 项）
    ├── test_agent_loop.py   # AgentLoop 测试（17 项）
    └── test_guardrails.py   # Guardrails + Tracing 测试（28 项）
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
