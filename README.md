# 自我进化

`astrbot_plugin_self_evolution` 是一个给 AstrBot 使用的认知增强插件，同时支持群聊和私聊，并已适配 NapCat 消息结构。

它的重点不是单独多几个命令，而是给机器人补上几层长期能力：

- 结构化人物记忆
- 会话事件与每日总结
- 会话反思与自我校准
- 群聊/私聊上下文注入
- 主动/被动社交参与
- 情感积分、SAN、表情包等行为增强

## 交流

- QQ 交流群：`1087272376`
- 群名称：`self_evolution插件交流反馈群`

## 功能分层

### 核心模块

- Prompt 注入
- 记忆系统
- 会话反思
- 调度任务
- 基础命令

### 可选模块

- 主动社交参与
- SAN 精力系统
- 情感积分底盘
- 管理辅助能力

### 实验模块

- 表情包与娱乐能力
- 人格进化

## 配置分组（5 层）

配置分为 5 组，按需查看：

| 分组 | 说明 | 典型配置 |
|------|------|----------|
| 基础 | 最常用，影响整体行为 | `persona_name`、`target_scopes`、`review_mode` |
| 记忆 | 记忆读写、画像、反思 | `memory_enabled`、`enable_profile_injection`、`reflection_enabled` |
| 行为 | 机器人表现，不影响数据 | `interject_enabled`、`affinity_auto_enabled`、`san_enabled` |

| 调试 | 按模块开关日志，不用全局大开 | `memory_debug_enabled`、`engagement_debug_enabled`、`affinity_debug_enabled` |

## 推荐先看的配置

如果觉得配置多，按这个顺序看：

1. 先决定哪些**分组**要开（基础/记忆/行为/实验各有总开关）
2. 再调对应分组的细参数
3. 排障时先开对应**调试**分组开关，不要全局开 `debug_log_enabled`

## 日志前缀速查

排障时看这些前缀定位模块：

| 前缀 | 模块 |
|------|------|
| `[MemoryWrite]` | 记忆写入路由决策 |
| `[MemoryQuery]` | 记忆查询分派与命中 |
| `[MemorySummary]` | 每日总结任务 |
| `[MemoryStore]` | 知识库存取 |
| `[MemoryInject]` | Prompt 注入命中 |
| `[Engagement]` | 场景判断、eligibility、plan |
| `[Affinity]` | 信号命中、积分变更 |

正常跳过（如 profile 未命中、summary 无消息）只打 `debug`，不打 `warning`。

## 核心能力

### 1. Prompt 注入

每次进入 LLM 前，插件会按需注入这些内容：

- 发送者和会话来源
- 引用、`@`、回复关系
- 群聊短期上下文
- 用户画像摘要
- 会话反思结果
- 长期知识库记忆
- SAN 和行为提示

主入口在 [main.py](main.py)。

### 2. 记忆系统

当前记忆系统已经重构成统一架构：

- `MemoryRouter`
  统一处理所有记忆写入
- `MemoryQueryService`
  统一处理所有记忆查询
- `MemoryTools`
  作为 LLM 工具与主链的适配层
- `SessionMemoryStore / SessionMemorySummarizer`
  负责会话事件、每日总结和知识库存取
- `ProfileManager / ProfileSummaryService`
  负责人物画像存取、构建和摘要

这一版的核心原则是：

- 所有写入先路由，再落库
- 所有读取先判断意图，再选策略
- 人物记忆、会话事件、每日总结和反思边界分开

## 记忆系统架构

### 1. 人物记忆

人物记忆只负责“这个人是谁、喜欢什么、长期特征是什么”。

它记录的内容包括：

- 身份信息
- 偏好
- 行为特征
- 最近变化
- 长期备注

相关文件：

- [engine/profile.py](engine/profile.py)
- [engine/profile_summary_service.py](engine/profile_summary_service.py)

### 2. 会话事件

会话事件只负责“这个群/私聊里发生过什么重要事件、约定、决定”。

适合记录：

- 约定
- 决定
- 群规
- 安排
- 重要结论

它不负责记录“这个人是什么样的人”。

### 3. 每日总结

每日总结只负责“某一天这个会话整体聊了什么”。

它适合回答：

- 昨天群里聊了什么
- 某天这个会话主要话题是什么

总结按 `scope` 隔离写入 AstrBot 知识库：

- 群聊：`<memory_kb_name>__scope__g_<group_id>`
- 私聊：`<memory_kb_name>__scope__p_<user_id>`

相关文件：

- [engine/session_memory_store.py](engine/session_memory_store.py)
- [engine/session_memory_summarizer.py](engine/session_memory_summarizer.py)

### 4. 反思记忆

反思只负责“机器人之后应该怎么调整回答方式”，不直接承担长期人物画像职责。

它主要产出：

- 自我校准提示
- 明确事实
- 会话日报

相关文件：

- [engine/reflection.py](engine/reflection.py)

## 记忆写入流程

现在所有新的记忆写入都会先进入 [engine/memory_router.py](engine/memory_router.py)。

`MemoryRouter` 会先判断内容属于哪类：

- 人物事实 -> 写入画像
- 会话事件 -> 写入会话事件记忆
- 反思提示 -> 不持久化
- 失败态/元话语 -> 直接丢弃

写入例子：

- “用户喜欢 Galgame” -> 人物画像
- “群里约好周日联机” -> session_event
- “昨天群里主要在讨论插件 bug” -> 每日总结

## 记忆读取流程

现在所有新的记忆读取都会先进入 [engine/memory_query_service.py](engine/memory_query_service.py)。

系统会先识别查询意图，再选择正确的读取策略：

- `recent_context`
  - 回答“刚刚/最近在聊什么”
- `daily_summary`
  - 回答“昨天/某天群里聊了什么”
- `session_event`
  - 回答“有没有约定过什么”
- `user_profile`
  - 回答“这个人是什么样”
- `user_message_history`
  - 回答“这个人以前说过什么”
- `fallback_kb`
  - 兜底语义检索

这意味着：

- 工具层和 Prompt 注入现在共用同一套读取规则
- 不再是每个功能自己决定查哪里

## 典型问题如何命中

- “刚刚你们在聊什么？”
  - 命中 `recent_context`
- “昨天这个群聊了什么？”
  - 命中 `daily_summary`
- “我们之前是不是约定过什么？”
  - 命中 `session_event`
- “你觉得这个用户是什么样的人？”
  - 命中 `user_profile`
- “他以前说过什么？”
  - 命中 `user_message_history`

## 主动与被动社交参与

互动系统现在已经重构成分层社交参与模型。

它不再只有"插嘴 / 不插嘴"两种状态，而是会根据群态和上下文规划参与等级：

- `IGNORE` — 不发言
- `REACT` — 发表情包
- `FULL` — 文本回复

同时会识别群态：

- `IDLE`
- `CASUAL`
- `HELP`
- `DEBATE`

决策时会检测锚点（anchor）来决定是否允许文本发言：无锚点时只能 IGNORE 或 REACT。

统一生成链路使用 `ContextBuilder` 复用同一套 prompt 注入逻辑（persona、identity、history、profile、memory、behavior），输出经 `OutputGuard` 审查，不合格自动降级表情包。

相关文件：

- [engine/eavesdropping.py](engine/eavesdropping.py)
- [engine/social_state.py](engine/social_state.py)
- [engine/engagement_planner.py](engine/engagement_planner.py)
- [engine/reply_executor.py](engine/reply_executor.py)
- [engine/generation_context.py](engine/generation_context.py)
- [engine/output_guard.py](engine/output_guard.py)
- [engine/speech_types.py](engine/speech_types.py)

## 情感积分

情感积分现在不再只依赖 LLM 主动调用工具。

它已经变成“自动底盘 + LLM 强信号修正”：

- 自动规则层负责日常弱信号
  - @bot
  - 回复 bot
  - 私聊发起
  - 礼貌词
  - 攻击词
  - 回访用户
- LLM 负责强信号修正
- 每日恢复由独立调度任务控制

相关文件：

- [engine/affinity.py](engine/affinity.py)
- [dao.py](dao.py)

## 可选与实验能力

### SAN 系统

用于模拟精力和疲劳感，会影响回复风格和活跃程度。

相关文件：

- [cognition/san.py](cognition/san.py)

### 娱乐与群菜单

表情包现在完全由本地资产目录管理，不再依赖数据库。

群菜单功能允许群友通过 `/addmeal` 和 `/delmeal` 维护菜品列表，并通过自然语言（吃啥、摆酒席等）触发随机推荐。

相关命令：
- `/addmeal <菜名>` - 添加菜品到群菜单（仅群聊）
- `/delmeal <菜名>` - 从群菜单删除菜品（仅群聊）

自然语言触发：
- 说"吃啥/吃什么/今天吃啥..."从菜单随机选一道推荐
- 说"摆酒席/开席/整一桌..."从菜单随机抽最多 10 道菜

相关文件：

- [engine/meal_store.py](engine/meal_store.py)
- [engine/entertainment.py](engine/entertainment.py)
- [commands/sticker.py](commands/sticker.py)

### 人格进化

人格进化审批流，适合管理者使用。

相关文件：

- [engine/persona.py](engine/persona.py)

## 最小安装步骤

1. 在 AstrBot 后台安装 `astrbot_plugin_self_evolution`
2. 创建一个基础知识库，并把名字设置为 `memory_kb_name`
3. 确保 AstrBot 已配置可用模型
4. 使用 NapCat 作为消息协议后端
5. 重载插件或重启 AstrBot

## 知识库说明

### 为什么还需要先建基础知识库

`memory_kb_name` 指向的知识库会作为基础入口使用：

- 作为长期记忆功能的锚点
- 作为后续自动创建 scope 隔离知识库的基础名

建议保留这个基础库，不要删除。

### AstrBot 怎么使用这些总结

如果当前会话已经启用知识库召回，插件会把当前 `scope` 自动绑定到对应的隔离知识库，让 AstrBot 优先召回当前群或当前私聊自己的总结。

## 命令

### 用户命令

- `/system help`
- `/system version`
- `/reflect`
- `/affinity show`
- `/san show`
- `/今日老婆`
- `/profile view [用户ID]`
- `/profile create [用户ID]`
- `/profile update [用户ID]`
- `/shut [分钟]`

说明：

- `/profile view` 现在是只读操作，不会隐式刷新画像
- 普通用户在私聊里只能操作自己
- 普通用户在群聊里也只能操作自己的画像

### 管理员命令

- `/affinity debug <用户ID>`
- `/set_affinity <用户ID> <分数>`
- `/san set [值]`
- `/profile delete <用户ID>`
- `/profile stats`
- `/evolution review [页码]`
- `/evolution approve <ID>`
- `/evolution reject <ID>`
- `/evolution clear`
- `/sticker list [页码]`
- `/sticker preview <UUID>`
- `/sticker delete <UUID>`
- `/sticker disable <UUID>`
- `/sticker enable <UUID>`
- `/sticker clear`
- `/sticker stats`
- `/sticker sync`
- `/sticker add`
- `/sticker migrate`
- `/db <操作>`

## LLM 工具

- `get_user_profile`
- `upsert_cognitive_memory`
- `get_user_messages`
- `get_group_recent_context`
- `get_group_memory_summary`
- `update_affinity`
- `evolve_persona`
- `list_tools`
- `toggle_tool`
- `list_stickers`
- `send_sticker`

## 数据存储位置

- `用户画像`
  - 本地文件
- `会话总结 / 会话事件`
  - AstrBot 知识库（按 scope 隔离）
- `反思 / 日报 / 情感积分 / SAN 等运行数据`
  - SQLite
- `表情包`
  - 本地目录资产库

## 配置分层

下面按配置面板的分组来说明。

### 基础

- `review_mode`
- `persona_name`
- `admin_users`
- `target_scopes`
- `debug_log_enabled`

### 核心开关

- `memory_enabled`
- `reflection_enabled`
- `interject_enabled`
- `san_enabled`
- `entertainment_enabled`

### 记忆

- `memory_kb_name`
- `memory_fetch_page_size`
- `memory_summary_chunk_size`
- `memory_summary_schedule`
- `enable_kb_memory_recall`

### 画像

- `profile_msg_count`
- `profile_cooldown_minutes`
- `enable_profile_injection`
- `enable_profile_fact_writeback`
- `auto_profile_enabled`
- `auto_profile_schedule`
- `auto_profile_batch_size`
- `auto_profile_batch_interval`
- `core_info_keywords`

### 反思

- `reflection_schedule`

### 关系

- `affinity_auto_enabled`
- `affinity_direct_engagement_delta`
- `affinity_friendly_language_delta`
- `affinity_hostile_language_delta`
- `affinity_returning_user_delta`
- `affinity_direct_engagement_cooldown_minutes`
- `affinity_friendly_daily_limit`
- `affinity_hostile_cooldown_minutes`
- `affinity_returning_user_daily_limit`
- `affinity_recovery_enabled`

### 互动

- `interject_interval`
- `interject_cooldown`
- `interject_trigger_probability`
- `engagement_react_probability`

### 行为

- `san_auto_analyze_enabled`
- `san_analyze_interval`
- `san_msg_count_per_group`
- `san_high_activity_boost`
- `san_low_activity_drain`
- `san_positive_vibe_bonus`
- `san_negative_vibe_penalty`

### 娱乐

- `entertainment_enabled`
- `sticker_learning_enabled`
- `sticker_freq_threshold`
- `sticker_total_limit`
- `meal_max_items`
- `meal_eat_keywords`
- `meal_banquet_keywords`
- `meal_banquet_count`
- `meal_banquet_cooldown_minutes`

## 重装与迁移注意事项

- 重装插件不会自动清空 AstrBot 知识库
- 重建数据库不会删除画像文件
- 重建数据库不会删除表情包目录
- scope 知识库会按群聊/私聊自动隔离，不需要手工逐个配置

如果你需要彻底清理不同层的数据，需要分别处理：

- 数据库：`/db reset` 或 `/db rebuild`
- 表情包：`/sticker clear` 或删除本地表情包目录
- 画像：删除画像文件
- 知识库总结：使用知识库清理或对应管理能力

## 开发者模块地图

### 写入主链

- [engine/memory_router.py](engine/memory_router.py)
  - 统一记忆写入路由

### 查询主链

- [engine/memory_query_service.py](engine/memory_query_service.py)
  - 统一记忆查询意图与分发
- [engine/memory_tools.py](engine/memory_tools.py)
  - LLM 工具与主链适配层

### 会话记忆

- [engine/session_memory_store.py](engine/session_memory_store.py)
  - 会话总结 / 事件的知识库存取
- [engine/session_memory_summarizer.py](engine/session_memory_summarizer.py)
  - 前一自然日消息抓取与每日总结

### 人物记忆

- [engine/profile.py](engine/profile.py)
  - 画像存取、构建（拉消息、选人、调 LLM、落盘）
- [engine/profile_summary_service.py](engine/profile_summary_service.py)
  - 画像摘要生成

### 入口与编排

- [main.py](main.py)
  - 工具注册、Prompt 注入编排、消息入口

## 当前完成度

如果只评价记忆系统本身，现在可以把它理解成：

- `架构完成度：8.5/10`
- `运行完成度：9/10`
- `产品完成度：7.5/10`

也就是说：

- 写入总线、查询中枢、分层存储已经落地
- 主链已经切到新架构
- 但后续仍然值得继续打磨 query intent、注入策略和文档表达
