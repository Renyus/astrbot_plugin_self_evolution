# AstrBot 框架工具调用机制分析文档

> 分析日期: 2026-03-13
> 框架版本: v4.19.2 (推测)
> 文档位置: \\wsl.localhost\Ubuntu\home\renyus\AstrBot

---

## 一、事件钩子 (Event Hooks)

### 1.1 可用钩子列表

| 钩子名称 | 触发时机 | 可用参数 | 用途 |
|----------|----------|----------|------|
| `on_astrbot_loaded` | AstrBot 加载完成 | - | 初始化插件 |
| `on_platform_loaded` | 平台加载完成 | - | 初始化平台相关 |
| `on_plugin_loaded` | 插件加载完成 | metadata | 插件初始化完成 |
| `on_plugin_unloaded` | 插件卸载完成 | metadata | 清理资源 |
| `AdapterMessageEvent` | 收到消息 | event | 处理消息 |
| `on_waiting_llm_request` | 等待 LLM（获取锁前） | event | 发送"思考中"提示 |
| `on_llm_request` | 收到 LLM 请求 | event, request | 修改 system prompt |
| `on_llm_response` | LLM 响应后 | event, response | 处理 LLM 响应 |
| `on_decorating_result` | 发送消息前 | event, result | 过滤/修改发送内容 |
| `on_using_llm_tool` | **调用函数工具前** | event, tool, tool_args | 仅通知，无法阻止 |
| `on_llm_tool_respond` | 调用函数工具后 | event, tool, tool_args, tool_result | 处理工具返回结果 |
| `on_after_message_sent` | 消息发送后 | event | 记录日志等 |
| `on_plugin_error` | 插件处理异常 | event, exception | 错误处理 |

### 1.2 导入方式

```python
from astrbot.api.event.filter import (
    on_llm_request,
    on_llm_response,
    on_llm_tool_respond,
    on_using_llm_tool,
    on_decorating_result,
    on_waiting_llm_request,
    after_message_sent,
)
```

---

## 二、工具调用流程

### 2.1 完整流程

```
1. LLM 决定调用工具
   ↓
2. on_using_llm_tool (工具调用前)
   - 参数: event, tool, tool_args
   - 仅通知，无法阻止/修改/替换
   ↓
3. 框架执行 MCP/函数工具
   ↓
4. on_llm_tool_respond (工具调用后)
   - 参数: event, tool, tool_args, tool_result
   - 可以读取/修改结果，但工具已执行
   ↓
5. LLM 收到工具返回结果
```

### 2.2 关键发现

**`on_using_llm_tool` 的限制**：
- 触发位置：`tool_loop_agent_runner.py:742`
- 仅有通知作用，框架不读取返回值
- 无法阻止工具调用
- 无法修改 tool_args（虽然可以修改传入的参数对象）

**`on_llm_tool_respond` 的限制**：
- 触发位置：`tool_loop_agent_runner.py:849`
- 工具已经执行完毕，MCP 调用已完成
- 只能读取/处理结果，无法避免 MCP 调用开销

---

## 三、图片处理机制

### 3.1 框架内置图片缓存

位置：`astrbot/core/agent/tool_image_cache.py`

```python
from astrbot.core.agent.tool_image_cache import tool_image_cache
```

- 用于缓存 MCP 返回的图片
- 不是图片描述缓存

### 3.2 MCP 工具 understand_image

- 这是用户配置的 MCP 工具
- 不是框架内置的
- 通过 `func_tool_manager.py` 加载
- 执行位置：`tool_executor.py`

---

## 四、当前图片缓存问题

### 4.1 问题描述

当图片已在缓存中时：
1. `on_llm_request` 阶段 → 检测到缓存 → 注入标签到 system prompt ✓
2. 框架仍然调用 MCP understand_image 工具 ✗
3. `on_llm_tool_respond` 阶段 → 捕获结果 → 存入/更新缓存 ✓

### 4.2 无法解决的原因

框架没有提供以下机制：
- 工具调用前拦截（`on_using_llm_tool` 无返回值）
- 工具结果替换（MCP 已执行完成）
- 基于条件的工具跳过

---

## 五、可行的优化方案

### 方案A：接受现状（当前实现）

**优点**：
- 实现简单
- 缓存正常工作
- 标签正确注入

**缺点**：
- MCP 仍会被调用（但结果可能被缓存覆盖）

### 方案B：Hack 方式 - 设置事件标记（不可行）

在 `on_using_llm_tool` 中设置标记：
```python
@on_using_llm_tool()
async def before_tool(self, event, tool, tool_args):
    # 设置标记
    event._skip_this_tool = True
```

**问题**：框架不读取这个标记，实际无效。

### 方案C：修改框架源码（需要框架配合）

在框架源码中添加拦截机制：
- 文件：`tool_loop_agent_runner.py`
- 位置：`on_tool_start` 调用处（742行）
- 添加：检查事件标记，如果标记存在则跳过工具执行

**需要修改的内容**：
1. 在 `AstrMessageEvent` 中添加 `_skip_tool` 属性
2. 在 `tool_loop_agent_runner.py` 中检查该属性
3. 如果为 True，直接返回缓存结果（需要插件提供缓存内容）

### 方案D：替换工具结果（部分优化）

在 `on_llm_tool_respond` 中，如果检测到缓存已有：
- 不存储重复缓存
- 直接返回已有结果

这不能避免 MCP 调用，但可以减少缓存重复。

---

## 六、相关源码文件

| 文件 | 用途 |
|------|------|
| `core/star/register/star_handler.py` | 钩子定义 |
| `core/star/star_handler.py` | EventType 枚举 |
| `core/agent/runners/tool_loop_agent_runner.py` | 工具执行流程 |
| `core/astr_agent_hooks.py` | 钩子调用入口 |
| `core/provider/func_tool_manager.py` | MCP 工具管理 |
| `core/agent/tool_executor.py` | 工具执行器 |

---

## 七、EventType 枚举值

位置：`core/star/star_handler.py`

```python
class EventType(enum.Enum):
    OnAstrBotLoadedEvent = enum.auto()           # AstrBot 加载完成
    OnPlatformLoadedEvent = enum.auto()          # 平台加载完成
    AdapterMessageEvent = enum.auto()             # 收到适配器发来的消息
    OnWaitingLLMRequestEvent = enum.auto()       # 等待调用 LLM（在获取锁之前，仅通知）
    OnLLMRequestEvent = enum.auto()               # 收到 LLM 请求
    OnLLMResponseEvent = enum.auto()              # LLM 响应后
    OnDecoratingResultEvent = enum.auto()         # 发送消息前
    OnCallingFuncToolEvent = enum.auto()          # 调用函数工具
    OnUsingLLMToolEvent = enum.auto()            # 使用 LLM 工具
    OnLLMToolRespondEvent = enum.auto()           # 调用函数工具后
    OnAfterMessageSentEvent = enum.auto()        # 发送消息后
    OnPluginErrorEvent = enum.auto()              # 插件处理消息异常时
    OnPluginLoadedEvent = enum.auto()             # 插件加载完成
    OnPluginUnloadedEvent = enum.auto()          # 插件卸载完成
```

---

## 八、工具调用相关代码位置

### 8.1 工具执行入口

文件：`tool_loop_agent_runner.py`

```python
# 742行 - 工具调用前钩子
await self.agent_hooks.on_tool_start(
    self.run_context,
    func_tool,
    valid_params,
)

# 750行 - 执行工具
executor = self.tool_executor.execute(
    tool=func_tool,
    run_context=self.run_context,
    **valid_params,
)

# 849行 - 工具调用后钩子
await self.agent_hooks.on_tool_end(
    self.run_context,
    func_tool,
    tool_args,
    tool_result,
)
```

### 8.2 钩子调用位置

文件：`astr_agent_hooks.py`

```python
# 工具调用前
async def on_tool_start(
    self,
    run_context: ContextWrapper[AstrAgentContext],
    tool: FunctionTool[Any],
    tool_args: dict | None,
):
    await call_event_hook(
        run_context.context.event,
        EventType.OnUsingLLMToolEvent,  # 这里是关键！
        tool,
        tool_args,
    )

# 工具调用后
async def on_tool_end(
    self,
    run_context: ContextWrapper[AstrAgentContext],
    tool: FunctionTool[Any],
    tool_args: dict | None,
    tool_result: CallToolResult | None,
):
    run_context.context.event.clear_result()
    await call_event_hook(
        run_context.context.event,
        EventType.OnLLMToolRespondEvent,
        tool,
        tool_args,
        tool_result,
    )
```

---

## 九、总结

1. **框架现状**：没有提供工具调用前拦截机制
2. **插件能力**：只能感知工具调用，无法阻止或跳过
3. **优化方向**：
   - 短期：接受现状，缓存功能正常工作
   - 长期：需要修改框架源码才能实现完全拦截
