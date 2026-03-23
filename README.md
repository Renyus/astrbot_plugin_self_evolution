# 自我进化

`astrbot_plugin_self_evolution` 是一个给 AstrBot 用的认知增强插件。

它的重点不是多几个命令，而是给机器人补上几层长期能力：
- 结构化人物记忆
- 长期会话记忆
- 会话反思与自我校准
- 群聊上下文注入
- 主动/被动互动策略

当前版本已经同时支持群聊和私聊，并针对 NapCat 的消息结构做了适配。

## 交流

- QQ 交流群：`1087272376`
- 群名称：`self_evolution插件交流反馈群`

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

支持群聊和私聊两种 scope。

相关文件：
- [engine/profile.py](/D:/skills/GD/astrbot_plugin_self_evolution/engine/profile.py)
- [commands/profile.py](/D:/skills/GD/astrbot_plugin_self_evolution/commands/profile.py)

### 3. 长期会话记忆

插件会按前一自然日汇总会话消息，写入 AstrBot 知识库，供后续召回。

现在的总结不是混写到一个库里，而是按 scope 隔离：
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

### 5. 主动和被动互动

插件支持两类互动：
- 被动互动：监听消息，根据关键词、引用、`@`、意愿积分决定是否接话
- 主动插嘴：定时检查群消息，在满足条件时主动参与

相关文件：
- [engine/eavesdropping.py](/D:/skills/GD/astrbot_plugin_self_evolution/engine/eavesdropping.py)

## 可选能力

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
3. 确保 AstrBot 已经配置好可用模型
4. 使用 NapCat 作为消息协议后端
5. 重载插件或重启 AstrBot

## 知识库说明

### 为什么还需要先建基础知识库

`memory_kb_name` 指向的知识库会作为基础入口使用：
- 作为长期记忆功能的锚点
- 作为后续自动创建 scope 隔离知识库的基础名

建议保留这个基础库，不要删除。

### AstrBot 怎么用到这些总结

如果当前会话已经启用知识库召回，插件会把当前 scope 自动绑定到对应的隔离知识库，让 AstrBot 优先召回当前群或当前私聊自己的总结。

## 命令

### 用户命令

- `/sehelp`
- `/version`
- `/reflect`
- `/affinity`
- `/今日老婆`
- `/view [用户ID]`
- `/create [用户ID]`
- `/update [用户ID]`
- `/shut [分钟]`

说明：
- `/view` 现在是只读操作，不会隐式刷新画像
- 普通用户在私聊里只能操作自己
- 普通用户在群聊里也只能操作自己的画像

### 管理员命令

- `/set_affinity <用户ID> <分数>`
- `/delete_profile <用户ID>`
- `/profile_stats`
- `/review_evolutions [页码]`
- `/approve_evolution <ID>`
- `/reject_evolution <ID>`
- `/clear_evolutions`
- `/sticker <操作>`
- `/db <操作>`

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

## 主要配置

下面只列当前仍在使用的主配置项。

### 记忆与画像

- `memory_kb_name`
- `memory_fetch_page_size`
- `memory_summary_chunk_size`
- `memory_summary_schedule`
- `reflection_schedule`
- `profile_msg_count`
- `profile_cooldown_minutes`
- `enable_profile_injection`
- `enable_profile_fact_writeback`
- `enable_kb_memory_recall`
- `target_scopes`
- `auto_profile_enabled`
- `auto_profile_schedule`
- `auto_profile_batch_size`
- `auto_profile_batch_interval`

### 主动互动

- `interject_enabled`
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

### SAN

- `san_enabled`
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
- 想把短期上下文、结构化画像和长期知识库记忆组合起来用
- 想让机器人在群里更像一个持续参与的角色，而不是纯问答接口
