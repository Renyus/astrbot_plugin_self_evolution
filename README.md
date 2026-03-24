# 自我进化

`astrbot_plugin_self_evolution` 是一个给 AstrBot 使用的认知增强插件。

它的重点不是多几个命令，而是给机器人补上几层长期能力：
- 结构化人物记忆
- 长期会话记忆
- 会话反思与自我校准
- 群聊/私聊上下文注入
- 主动/被动互动策略

当前版本同时支持群聊和私聊，并已适配 NapCat 消息结构。

## 交流

- QQ 交流群：`1087272376`
- 群名称：`self_evolution插件交流反馈群`

## 功能分层

### 核心模块

- Prompt 注入
- 人物画像
- 长期记忆
- 会话反思
- 调度任务
- 基础命令

### 可选模块

- 主动插嘴
- SAN 精力系统
- 管理辅助能力

### 实验模块

- 表情包与娱乐能力
- 元编程
- 人格进化

## 先看总开关

如果你觉得配置多，建议先只看这几个模块总开关：

- `memory_enabled`：是否启用长期记忆模块
- `reflection_enabled`：是否启用反思与日报模块
- `entertainment_enabled`：是否启用娱乐与表情包模块
- `meta_enabled`：是否启用元编程相关模块
- `interject_enabled`：是否启用主动插嘴
- `san_enabled`：是否启用 SAN 精力系统

推荐的使用顺序是：

1. 先决定哪些模块要开
2. 再调对应模块的细参数
3. 没用到的模块先关掉，不要一开始全调

## 核心能力

### 1. Prompt 注入

每次进入 LLM 前，插件会按需注入这些内容：

- 发送者和会话来源
- 引用、`@`、回复关系
- 群聊短期历史
- 用户画像摘要
- 会话反思结果
- 长期知识库记忆
- SAN 和行为提示

主入口在 [main.py](/D:/skills/GD/astrbot_plugin_self_evolution/main.py)。

### 2. 结构化人物记忆

插件会为用户维护结构化画像，记录：

- 身份信息
- 偏好
- 行为特征
- 最近变化
- 长期备注

支持群聊和私聊两种 `scope`。

相关文件：
- [engine/profile.py](/D:/skills/GD/astrbot_plugin_self_evolution/engine/profile.py)
- [commands/profile.py](/D:/skills/GD/astrbot_plugin_self_evolution/commands/profile.py)

### 3. 长期会话记忆

插件会按前一自然日汇总会话消息，写入 AstrBot 知识库，供后续召回。

现在的总结不是混写到一个库里，而是按 `scope` 隔离：

- 群聊：`<memory_kb_name>__scope__g_<group_id>`
- 私聊：`<memory_kb_name>__scope__p_<user_id>`

这样可以避免群聊和私聊互相污染。

相关文件：
- [engine/memory.py](/D:/skills/GD/astrbot_plugin_self_evolution/engine/memory.py)
- [scheduler/tasks.py](/D:/skills/GD/astrbot_plugin_self_evolution/scheduler/tasks.py)

### 4. 会话反思

插件支持会话反思和每日批处理，主要负责：

- 自我校准
- 提取明确事实
- 刷新活跃用户画像
- 生成会话日报

相关文件：
- [engine/reflection.py](/D:/skills/GD/astrbot_plugin_self_evolution/engine/reflection.py)

### 5. 主动与被动互动

插件支持两类互动：

- 被动互动：监听消息，根据关键词、引用、`@`、意愿积分决定是否接话
- 主动插嘴：定时检查群消息，在满足条件时主动参与

相关文件：
- [engine/eavesdropping.py](/D:/skills/GD/astrbot_plugin_self_evolution/engine/eavesdropping.py)

## 可选与实验能力

### SAN 系统

用于模拟精力和疲劳感，会影响回复风格和活跃程度。

文件：
- [cognition/san.py](/D:/skills/GD/astrbot_plugin_self_evolution/cognition/san.py)

### 表情包与娱乐

提供表情包学习、发送和轻量娱乐功能。

文件：
- [engine/entertainment.py](/D:/skills/GD/astrbot_plugin_self_evolution/engine/entertainment.py)
- [commands/sticker.py](/D:/skills/GD/astrbot_plugin_self_evolution/commands/sticker.py)

### 元编程与人格进化

这部分更偏实验性，适合管理员在测试环境中使用。

文件：
- [engine/meta_infra.py](/D:/skills/GD/astrbot_plugin_self_evolution/engine/meta_infra.py)
- [engine/persona.py](/D:/skills/GD/astrbot_plugin_self_evolution/engine/persona.py)

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

### AstrBot 怎么用到这些总结

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

说明：
- `/san show` 所有人可用，用于查看当前 SAN 状态
- `/san set` 仅管理员可用；不带参数时显示当前精力值和状态，带参数时设置为指定值
- `/sticker list [页码]` 支持分页查看
- **表情包现已完全由本地资产目录管理**（`data/self_evolution/stickers/`），不再使用数据库存储
- **删除数据库不会影响表情包**，表情包文件独立存储在本地目录

## LLM 工具

- `get_user_profile`
- `upsert_cognitive_memory`
- `get_user_messages`
- `update_affinity`
- `evolve_persona`
- `list_tools`
- `toggle_tool`
- `get_plugin_source`
- `update_plugin_source`
- `list_stickers`
- `send_sticker`

其中 `get_user_messages` 现在会尽量按目标用户消息条数返回结果，而不是只从最近一小段群消息里做浅筛。

## 配置分层

下面按配置面板的分组来说明。

### 基础

这一组是最先该看的配置。

- `review_mode`
- `persona_name`
- `admin_users`
- `critical_keywords`
- `target_scopes`
- `debug_log_enabled`

### 核心开关

先决定模块要不要开，再去调细参数。

- `memory_enabled`
- `reflection_enabled`
- `interject_enabled`
- `san_enabled`
- `entertainment_enabled`
- `meta_enabled`

### 记忆

负责长期会话记忆和知识库召回。

- `memory_kb_name`
- `memory_fetch_page_size`
- `memory_summary_chunk_size`
- `memory_summary_schedule`
- `enable_kb_memory_recall`

### 画像

负责人物记忆和自动建档。

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

负责每日反思、日报和画像刷新。

- `reflection_schedule`

### 关系

负责关系温度底盘和自动好感度调整。

- `affinity_auto_enabled`
- `affinity_direct_engagement_delta`
- `affinity_friendly_language_delta`
- `affinity_hostile_language_delta`
- `affinity_returning_user_delta`
- `affinity_direct_engagement_cooldown_minutes`
- `affinity_friendly_daily_limit`
- `affinity_hostile_cooldown_minutes`
- `affinity_returning_user_daily_limit`

### 互动

这组控制主动插嘴和被动互动判定。

- `interject_interval`
- `interject_cooldown`
- `interject_min_msg_count`
- `interject_silence_timeout`
- `interject_local_filter_enabled`
- `interject_require_at`
- `interject_urgency_threshold`
- `interject_dry_run`
- `interject_trigger_probability`
- `interject_analyze_count`
- `eavesdrop_message_threshold`
- `eavesdrop_threshold_min`
- `eavesdrop_threshold_max`

### 行为

这组控制 SAN、漏斗积分和行为倾向。

- `san_max`
- `san_cost_per_message`
- `san_recovery_per_hour`
- `san_low_threshold`
- `san_auto_analyze_enabled`
- `san_analyze_interval`
- `san_msg_count_per_group`
- `san_high_activity_boost`
- `san_low_activity_drain`
- `san_positive_vibe_bonus`
- `san_negative_vibe_penalty`
- `leaky_integrator_enabled`
- `leaky_decay_factor`
- `leaky_trigger_threshold`
- `interest_boost`
- `daily_chat_boost`
- `desire_cooldown_messages`
- `desire_cooldown_seconds`
- `dropout_enabled`
- `dropout_edge_rate`

## 数据库维护

管理员现在可以使用下面这组命令维护插件数据库：

- `/db show`
- `/db reset`
- `/db rebuild`
- `/db confirm`

说明：

- `/db reset` 是清空数据库中的业务数据，数据库文件仍然保留。
- `/db rebuild` 是删除插件数据库文件后重新建库，这不是清空表，而是直接重建数据库文件。
- `/db confirm` 会确认最近一次 `reset` 或 `rebuild` 请求，确认窗口为 30 秒。
- 这组命令仅管理员可用。

推荐使用场景：

- 数据脏了，但结构正常：优先用 `/db reset`
- 数据库表结构异常、文件损坏、升级后出现持续性 sqlite 报错：再用 `/db rebuild`

## 重装插件注意事项

如果你准备卸载后重新安装，建议先确认你想保留哪些数据。

会保留或需要你特别注意的内容：

- 插件数据库文件：里面有好感度、会话反思、日报、表情包等数据
- 用户画像文件：这是独立于数据库之外的本地画像数据
- AstrBot 知识库：长期会话总结写在知识库里，不会因为重装插件自动消失
- 配置项：重装后请重新核对是否和当前版本字段一致

建议的重装顺序：

1. 先决定是否保留旧数据库和旧画像
2. 如果只想清业务数据但保留插件本体，优先使用 `/db reset`
3. 如果怀疑数据库文件本身损坏，再使用 `/db rebuild`
4. 如果你希望彻底重新开始，除了执行 `/db rebuild`，还要手动清理画像目录和对应知识库内容
5. 重装后确认 `memory_kb_name` 仍然指向你想使用的基础知识库
6. 重装后建议手动测试一次：
   - `/system version`
   - `/san show`
   - `/db show`
   - `/profile view`

额外提醒：

- 当前版本的长期总结是按 scope 写入知识库的，所以“重装插件”和“删除知识库总结”不是同一件事。
- 如果你只删了数据库文件，没有清知识库，AstrBot 仍可能召回旧的会话总结。
- 如果你只重装插件但保留画像文件，机器人依然会沿用旧画像。
- `surprise_enabled`
- `surprise_boost_keywords`
- `inner_monologue_enabled`
- `boredom_enabled`
- `boredom_consecutive_count`

### 实验

这组建议按需开启，不建议一开始全开。

- `debate_enabled`
- `debate_rounds`
- `debate_system_prompt`
- `debate_criteria`
- `debate_agents`
- `allow_meta_programming`

### 娱乐

这组负责表情包和轻量娱乐能力。

- `sticker_learning_enabled`
- `sticker_target_qq`
- `sticker_daily_limit`
- `sticker_total_limit`
- `sticker_send_cooldown`
- `sticker_freq_threshold`

### 提示

这组直接影响 prompt 注入行为。

- `disable_framework_contexts`
- `inject_group_history`
- `group_history_count`
- `max_prompt_injection_length`
- `prompt_meltdown_message`

## 数据存储

- 用户画像：本地文件
- 关系、反思、日报、好感度、表情包：SQLite
- 长期会话总结：AstrBot 知识库

## 环境要求

- AstrBot
- NapCat
- 至少一个可用模型 Provider
- 一个与 `memory_kb_name` 对应的基础知识库

## 适合什么场景

- 想让机器人记住用户长期偏好和身份信息
- 想让机器人对群聊和私聊有跨天记忆
- 想把短期上下文、结构化画像和长期知识库记忆组合起来使用
- 想让机器人在群里更像一个持续参与的角色，而不是纯问答接口
