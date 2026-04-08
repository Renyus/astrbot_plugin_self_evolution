# 自我进化

`astrbot_plugin_self_evolution` 是 AstrBot 的长期能力增强插件，适配群聊、私聊和 NapCat 消息结构。

目标是让 Bot 在无对话时也在"变化"——有情绪起伏、有社交需求、有待办挂念、有记忆延续，而不是每次对话都是一张白纸。

---

## 功能架构

### Persona Sim 2.0 — 人格生活模拟（核心引擎）

内置人格状态引擎，追踪 **能量、心情、社交需求、饱腹感**，基于时间差推演状态变化，触发短期 Effect 和脑内待办。**SAN 系统现已归口到 Persona Sim**，不再独立维护能量值。

#### Prompt 注入

- **System Prompt**：人格叙事片段（`snapshot_to_prompt`）+ 人格设定基底
  - 输出示例：`"刚主动说了话但被冷落，还有点堵着"`
  - 不再注入"精力充沛"等状态基调，由 Persona Sim 统一提供
- **注入位置**：`generation_context.py` → `_get_persona_prompt()`

#### 核心数据

| 状态 | 说明 |
|------|------|
| `energy` | 活力，0~100，影响回复意愿 |
| `mood` | 心情，0~100，影响语气 |
| `social_need` | 社交需求，0~100 |
| `satiety` | 饱腹感，0~100 |

#### Effect 系统

- **来源语义**：每个 Effect 携带 `source_detail`（触发来源描述）、`decay_style`（衰减风格）、`recovery_style`（恢复风格），持久化到 SQLite
- **wronged + active + miss** → `source_detail="主动搭话但被冷落，期望落空"`
- **互动维度**：`InteractionMode`（active/passive）× `InteractionOutcome`（connected/missed）

#### 待办生成（脑内关切）

- **need_todo（生理型）**：饿、累、想安静
- **social_todo（关系型）**：想把没说完的话接上、想继续聊、想躲热闹
- `wronged + active + missed` → `"想把当时没说完的话接上"`

#### 日结轨迹

分析日内 missed / connected / active 计数，输出情感 trajectory（向上/有落差/独处/平淡/平稳）

#### 相关文件

- `engine/persona_sim_engine.py` — 引擎核心
- `engine/persona_sim_rules.py` — Effect 触发规则
- `engine/persona_sim_injection.py` — Prompt 注入逻辑
- `engine/persona_sim_todo.py` — 待办生成
- `engine/persona_sim_consolidation.py` — 日结

---

### SAN × Persona Sim 统一

LLM 群分析结果（activity / emotion / has_drama）映射为 `interaction_quality`，通过 `persona_sim.tick(quality)` 注入，不再独立改 SAN 值。

#### Quality 映射

| 分析结果 | interaction_quality |
|----------|-------------------|
| drama=True | `bad` |
| low + negative | `bad` |
| low | `awkward` |
| negative | `awkward` |
| high + positive | `good` |
| 其他 | `normal` |

#### Prompt 注入

- **当 Persona Sim 可用时**：`get_prompt_injection()` 返回空串，不注入任何 SAN 描述
- **当 Persona Sim 不可用时**：降级为独立能量系统，注入 `【当前状态】精力充沛/略有疲态/疲惫不堪`

#### 相关文件

- `cognition/san.py` — 群消息 LLM 分析
- `scheduler/` — `SANAnalyze` 定时任务

---

### 主动社交参与（Active Engagement 4.0）

Bot 主动判断时机和内容，通过 Pending Opportunity 机制在满足条件时主动插话。

#### 核心文件

- `engine/opportunity_cache.py` — PendingOpportunity 缓存，支持 warm/peek/consume，OpportunityScore 计算，ActiveMotive 驱动
- `engine/interject_preferences.py` — 用户主动互动偏好（initiative trend + outcome tracking）
- `engine/eavesdropping.py` — 主动/被动社交入口，`check_engagement` 决策点
- `engine/engagement_planner.py` — 场景分类、意图规划、评分、姿态计算
- `engine/reply_intent.py` — ReplyIntent 构造
- `engine/reply_executor.py` — 回复执行
- `engine/reply_policy.py` — 统一仲裁
- `engine/reply_recorder.py` — 回复结果记录（用于 initiative trend）

#### 评分与分层

`OpportunityScore.level_from_score()` 使用固定阈值：

| 分数区间 | 层级 |
|---------|------|
| < 0.15 | `ignore` |
| 0.15–0.25 | `react` |
| 0.25–0.35 | `text_lite` |
| ≥ 0.35 | `full` |

sub_scene 影响评分权重（乘法因子），不影响层级阈值。

#### Motive 驱动锚点选择

`_best_anchor_for_score` 根据 ActiveMotive 优先级选择锚点：

| Motive | 锚点优先级 |
|--------|-----------|
| `CONTINUE_THREAD` | 未完成 cue > thread > topic_hook |
| `CURIOUS_PROBE` | question > topic_hook |
| `SEEK_CONNECTION` | trigger_text > joke |
| `SELF_PROTECTIVE` | natural_landing > topic_hook |
| `LIGHT_RELIEF` | joke > trigger_text |

#### UnfinishedCue 系统

`ConversationMomentum.unfinished_cues` 追踪未完成互动线索：

- **TTL**：180 秒
- **类型**：`unanswered_question` / `unfinished_joke` / `unfinished_topic`
- **触发**：`bot_spoke` 清除 `unanswered_question`，`reset_wave` 清除 `unfinished_joke`
- **持久化**：通过 DAO 写入 `engagement_state.unfinished_cues`（JSON 数组）

#### TEXT_LITE 子变体

`TextLiteVariant` 枚举提供三种轻量回复变体：

| 变体 | 场景 | max_chars |
|------|------|-----------|
| `QUICK_TOUCH` | JOYFUL_BUSTLE / 轻松氛围 | ~30 |
| `QUIET_FOLLOW` | AWKWARD_GAP / 收敛姿态 | ~40 |
| `SMALL_PROBE` | TOPIC_FOCUS / 好奇探问 | ~50 |

`SpeechDecision.text_lite_variant` 字段携带变体信息，生成提示词包含对应风格指导。

#### Relation 升级

`_score_relation` 综合两个信号：

1. **静态好感度**：`trigger_user_affinity`（来自 Affinity 系统）
2. **近期互动余韵**：`recent_interaction_outcome`（connected/missed/awkward/relief）

connected 情境下 boost，missed/awkward 情境下 penalize。

#### DAO Schema

`engagement_state` 表新增列：

```sql
last_unanswered_question TEXT
last_unfinished_joke TEXT
unfinished_cues TEXT DEFAULT '[]'
```

#### Phase 1 Bug Fixes（已修复）

- **consume 逻辑**：`consume_high_score` 先 peek 确认高优质机会，再 remove_one 移除（避免 policy 阻塞的机会被误消费）
- **trigger user 信息**：PendingOpportunity 新增 `trigger_user_id` / `trigger_user_name`，主动 intent 可获取真实触发者
- **remove_one**：OpportunityCache 新增 `remove_one(scope_id, opp)`，只移除特定 opportunity，不清空整个 scope

---

### 社交参与 Prompt 注入

- **主动模式**：`[主动发言模式]` + "你是主动加入群聊讨论的…保持简短（50字以内）"
- 注入位置：`_build_behavior()` → 当 `decision.delivery_mode == "text" && text_mode == "interject"`

#### 参与层级

| 层级 | 行为 |
|------|------|
| `IGNORE` | 不回应 |
| `REACT` | 表情包 reaction |
| `FULL` | LLM 文本回复 |

#### Planner 微调

- `wronged` + 主动 → initiative ↓
- `social_todo` 存在 → initiative ↑
- `curious` + active → playfulness ↑

---

### 记忆与画像

维护用户画像（身份、偏好、特征、备注）和会话事件/每日总结，支持按群聊/私聊自动 scope 隔离。长期知识库可召回并注入 Prompt。

#### Prompt 注入

- **`【内部参考信息】`**：发送者ID、昵称、情感积分、群聊/私聊来源、引用/AT上下文
  - 注入位置：`_build_identity()`
- **`【群消息历史】`**：最近 N 条群消息（`inject_group_history` 控制）
  - 注入位置：`_build_history()`
- **画像摘要**：身份、偏好、特征、长期笔记（`enable_profile_injection` 控制，需 AT 或回复）
  - 注入位置：`_build_profile()`
- **知识库召回**：记忆段落召回（`enable_kb_memory_recall` 控制）
  - 注入位置：`_build_memory()`

#### 相关文件

- `engine/profile.py` — 画像增删改查
- `engine/memory_router.py` / `memory_query_service.py` — 记忆查询与注入
- `engine/session_memory_store.py` — 会话事件持久化
- `engine/session_memory_summarizer.py` — 每日会话总结

---

### 审核与娱乐

#### Prompt 注入

- **表情包学习提示**：`[表情包学习]` + 触发关键词说明
  - 注入位置：`_build_behavior()` → 当 `sticker_learning_enabled`
- **画像更新提示**：`[即时画像更新提示]` + "请调用 upsert_cognitive_memory 工具"
  - 注入位置：`_build_behavior()` → 当 `_should_inject_preference_hints()`

#### 相关文件

- `engine/entertainment.py` — 表情包学习 + `get_prompt_injection()`
- `engine/caption_service.py` / `moderation_classifier.py` — 图片 caption + NSFW/Promo 审核
- `engine/sticker_store.py` — 表情包存储与发送
- `engine/meal_store.py` — 群菜单

---

### 认知与情感

- `engine/affinity.py` — 情感积分（直接回复/礼貌词/攻击词/回访加分），不注入 Prompt
- `scheduler/` — 每日反思批处理、好感度恢复等定时任务

---

## Persona Sim 2.0

从"连续状态引擎"升级为"人格生活模拟系统"，新增 6 大能力：

### A. Effect 来源语义

每个 Effect 携带来源描述（`source_detail`）、衰减风格（`decay_style`）和恢复风格（`recovery_style`），存储于 SQLite `persona_effects` 表，持久化不丢失。

示例：`wronged` + `active` + `missed` → `source_detail="主动搭话但被冷落，期望落空"`

### B. 互动语义维度

引入 `InteractionMode`（active / passive）× `InteractionOutcome`（connected / missed）两个维度，替代原本单一 `quality`。互动事件携带完整 mode × outcome 记录，影响 Effect 触发和待办生成。

### C. Todo 分类（need_todo / social_todo）

待办从单一列表升级为两种类型：
- **need_todo**（`TodoType.INTERNAL`）：生理需求型——饿、累、想安静
- **social_todo**（`TodoType.SOCIAL`）：关系型——想把没说完的话接上、想继续聊、想躲热闹

`wronged` + `active` + `missed` 生成 social_todo `"想把当时没说完的话接上"`；`lonely` + `recent_connected` 生成 `"刚才那通还没聊够"`。

### D. Prompt 叙事风格

注入 Prompt 的不再是状态数字和状态基调，而是一段心理叙事——从近期事件中提取 interaction mode × outcome，生成短小、有情绪连贯性的描述，例如：`"刚主动说了话但被冷落，还有点堵着"`。

不再说"精力充沛"或"有点提不起劲"这类和 SAN 系统冲突的基调。

### E. 日结情感轨迹

日结从"互动 N 次"进化为情感轨迹固化：分析日内 missed / connected / active 计数，输出 trajectory（向上 / 有落差 / 独处 / 平淡 / 平稳），`shift_hint` 反映日内落差感。

### F. Planner 微调

`plan_engagement` 读取近期 interaction_outcome / interaction_mode，动态调整 warmth / initiative / playfulness。`wronged` + `主动` 感降低主动意愿；`social_todo` 存在时提高 initiative 偏向。

### DAO Schema 升级

`persona_effects` 表新增 3 列：`source_detail`、`decay_style`、`recovery_style`，通过 ALTER TABLE 迁移（向后兼容）。

---

## 定时任务调度

任务错开执行，避免深夜~凌晨 LLM 调用集中：

| 时间 | 任务 | 说明 |
|------|------|------|
| `0 1 * * *` | PersonaThought | 人格思维生成（每12小时） |
| `0 4 * * *` | ProfileCleanup | 清理过期用户画像 |
| `0 5 * * *` | PersonaConsolidation | 人格日结 |
| `0 6 * * *` | MemorySummary | 每日会话记忆总结 |
| `*/{interval} * * *` | Interject | 主动插嘴检查 |
| `*/{interval} * * *` | SANAnalyze | SAN 精力分析 |
| `{profile_schedule}` | ProfileBuild | 批量构建用户画像 |
| `{reflection_schedule}` | DailyReflection | 每日反思批处理 |
| `{github_check_schedule}` | GitHubCheck | 检查 GitHub 仓库更新，有新 commit 时推送到指定群 |

---

## GitHub 更新通知

插件可监控指定 GitHub 仓库指定分支，当检测到新 commit 时向目标群或用户发送通知。

### 配置项

- `update_notify_repo` — 仓库路径，格式 `owner/repo`（默认 `Renyus/astrbot_plugin_self_evolution`）
- `update_notify_branch` — 分支名（默认 `master`）
- `update_notify_group_id` — 接收通知的群 ID 列表（群聊）
- `update_notify_user_ids` — 接收通知的用户 ID 列表（私聊）
- `update_check_interval` — 检查间隔（分钟，默认 30）

> 留空 `update_notify_group_id` 和 `update_notify_user_ids` 则不启用通知。

### 首次运行保护

首次检测到 commit 时不会发送通知（防止首次运行推送大量历史 commit），之后仅在新 commit 出现时通知。使用 `/db github_reset` 可重置缓存，强制重新检测。

---

## 戳一戳互动

当收到群友或私聊用户的戳一戳（poke）时，Bot 会根据配置的概率做出反应。

### 行为模式

- **戳回去**：以一定概率反戳对方
- **发送吐槽**：不戳回时随机发送一条吐槽文案

### 配置项

- `poke_reply_enabled` — 是否启用戳回复（默认开启）
- `poke_poke_back_chance` — 戳回去的概率 1~100（默认 50）
- `poke_complaint_texts` — 吐槽文案列表（默认：干嘛呢~、有事说事！、别闹、正经点）

### 注意

由于 NapCat 发送的 poke 事件无法区分戳/抱/亲，吐槽文案使用中性措辞，不涉及具体动作。

---

## 命令参考

### 用户命令

| 命令 | 说明 |
|------|------|
| `/se help` | 查看指令帮助 |
| `/se version` | 查看插件版本 |
| `/reflect` | 触发反思 |
| `/af show` | 查看好感度 |
| `/san show` | 查看 SAN 状态 |
| `/profile view [用户]` | 查看画像（不传则看自己的） |
| `/profile create [用户]` | 创建画像（仅管理员可指定他人） |
| `/profile update [用户]` | 更新画像（仅管理员可指定他人） |
| `/addmeal <菜名>` | 添加菜品到群菜单 |
| `/delmeal <菜名>` | 删除菜品 |
| `/今日老婆` | 随机抽取一名群友 |
| `/feed` | 喂食（发送图片后使用，识图判断食物并更新饱腹感/心情） |
| `/shut [分钟]` | 让 AI 暂时闭嘴（管理员） |

### 管理员命令

| 命令 | 说明 |
|------|------|
| `/af debug <用户>` | 好感度调试（@或ID） |
| `/af set <用户> <分数>` | 设定好感度（@或ID） |
| `/san set [值]` | 设定 SAN 值 |
| `/profile delete <用户>` | 删除画像（@或ID） |
| `/profile stats` | 画像统计 |
| `/meal ban <用户>` | 禁止加菜（@或ID） |
| `/meal unban <用户>` | 解除禁止（@或ID） |
| `/ev review [页码]` | 审核流 |
| `/ev approve <ID>` | 批准 |
| `/ev reject <ID>` | 拒绝 |
| `/ev clear` | 清空 |
| `/ev stats [群ID]` | 进化统计 |
| `/ps state [群]` | 只读人格状态 |
| `/ps status [群]` | 触发 tick 后查看人格快照 |
| `/ps tick [quality] [群]` | 手动推进人格时间（none/negative/positive） |
| `/ps todo [群]` | 查看脑内待办 |
| `/ps effects [群]` | 查看活跃效果 |
| `/ps apply [q] [群]` | 应用互动影响（q: bad/awkward/normal/good/relief/brief） |
| `/ps today [群]` | 查看今日人格摘要 |
| `/ps consolidate [群] [日期]` | 执行人格日结（格式: YYYY-MM-DD） |
| `/ps think [群]` | 手动触发 LLM 生成内心独白 |
| `/sticker list [页码]` | 表情包列表 |
| `/sticker preview <UUID>` | 预览 |
| `/sticker delete <UUID>` | 删除 |
| `/sticker disable <UUID>` | 禁用 |
| `/sticker enable <UUID>` | 启用 |
| `/sticker clear` | 清空 |
| `/sticker stats` | 统计 |
| `/sticker sync` | 同步 |
| `/sticker add` | 添加 |
| `/sticker migrate` | 迁移 |
| `/db show` | 查看数据库 |
| `/db reset` | 重置数据库 |
| `/db rebuild` | 重建数据库 |
| `/db confirm` | 确认操作 |
| `/db github_reset` | 重置 GitHub 更新缓存（强制重新检测） |
| `/kb clear [scope/all]` | 清空知识库文档（仅管理员） |

---

## 配置说明

### 基础开关

- `review_mode` — 管理员审核模式
- `persona_name` — 人格名称
- `admin_users` — 管理员白名单
- `target_scopes` — 目标群/私聊白名单

### 核心模块开关

- `memory_enabled` — 记忆模块
- `reflection_enabled` — 反思模块
- `interject_enabled` — 主动插话
- `san_enabled` — SAN 系统
- `entertainment_enabled` — 娱乐模块

### 记忆与画像

- `memory_kb_name` — 知识库名称
- `memory_fetch_page_size` — 召回分页大小
- `memory_summary_chunk_size` — 总结 chunk 大小
- `memory_summary_schedule` — 总结计划（默认 06:00）
- `enable_kb_memory_recall` — 召回记忆
- `memory_query_fallback_enabled` — KB 检索兜底
- `profile_msg_count` — 画像消息阈值
- `profile_cooldown_minutes` — 画像冷却（分钟）
- `enable_profile_injection` — 注入画像摘要
- `enable_profile_fact_writeback` — 写回新事实
- `auto_profile_enabled` — 自动构建画像
- `auto_profile_schedule` — 画像构建计划
- `auto_profile_batch_size` — 每批处理群数
- `auto_profile_batch_interval` — 批次间隔（分钟）

### Prompt 注入

- `disable_framework_contexts` — 禁用框架上下文
- `inject_group_history` — 注入群聊历史
- `group_history_count` — 历史注入条数
- `max_prompt_injection_length` — 注入最大长度
- `surprise_enabled` — 启用惊奇检测
- `surprise_boost_keywords` — 惊奇关键词表
- `dropout_enabled` — 启用随机留白
- `dropout_edge_rate` — 随机留白概率

### 行为与互动

- `affinity_auto_enabled` — 自动好感度更新
- `affinity_recovery_enabled` — 好感度每日恢复
- `affinity_direct_engagement_delta` — 主动互动加分值
- `affinity_friendly_language_delta` — 礼貌语言加分值
- `affinity_hostile_language_delta` — 攻击语言扣分值
- `affinity_returning_user_delta` — 回访用户加分值
- `affinity_direct_engagement_cooldown_minutes` — 互动冷却（分钟）
- `affinity_friendly_daily_limit` — 礼貌词每日上限
- `affinity_hostile_cooldown_minutes` — 攻击冷却（分钟）
- `affinity_returning_user_daily_limit` — 回访每日上限
- `interject_enabled` — 启用主动插嘴
- `interject_interval` — 插嘴检查间隔（分钟）
- `interject_cooldown` — 插嘴冷却（秒）
- `interject_trigger_probability` — 插嘴触发概率
- `engagement_react_probability` — emoji reaction 概率

### SAN 精力系统

- `san_enabled` — 启用 SAN 系统
- `san_max` — SAN 最大值
- `san_cost_per_message` — 每条消息耗 SAN
- `san_recovery_per_hour` — 每小时恢复 SAN
- `san_low_threshold` — 低 SAN 阈值
- `san_auto_analyze_enabled` — 启用 SAN 分析
- `san_analyze_interval` — SAN 分析间隔（秒）
- `san_msg_count_per_group` — 每群分析条数

### 审核（moderation）

- `moderation.enabled` — 启用审核
- `moderation.enforcement_enabled` — 执行处罚
- `moderation.nsfw_keywords` — NSFW 关键词
- `moderation.promo_keywords` — 引流推广关键词
- `moderation.refusal_keywords` — 模型拒绝描述关键词
- `moderation.nsfw_refusal_confidence` — NSFW 拒绝描述置信度
- `moderation.promo_refusal_confidence` — 引流拒绝描述置信度
- `moderation.weak_keyword_confidence` — 关键词匹配置信度
- `moderation.confidence_threshold` — 置信度门槛
- `moderation.escalation_threshold` — 踢人阈值
- `moderation.ban_duration_minutes` — 禁言时长（分钟）
- `moderation.nsfw_warning_message` — NSFW 警告消息
- `moderation.nsfw_ban_reason_message` — NSFW 处罚理由消息
- `moderation.promo_warning_message` — 引流警告消息
- `moderation.promo_ban_reason_message` — 引流处罚理由消息

### 表情包与娱乐

- `sticker_learning_enabled` — 学习表情包
- `sticker_target_qq` — 学习目标 QQ 列表
- `sticker_total_limit` — 表情包总数上限
- `sticker_send_cooldown` — 发送冷却（分钟）
- `sticker_freq_threshold` — 学习频次阈值
- `sticker_reply_enabled` — 回复附加表情包
- `sticker_reply_chance` — 触发概率（1~100）
- `sticker_reply_max_per_hour` — 每小时上限
- `sticker_reply_min_text_length` — 最低文字长度
- `meal_max_items` — 菜单最大条目
- `meal_eat_keywords` — 吃饭关键词
- `meal_banquet_keywords` — 设宴关键词
- `meal_banquet_count` — 设宴触发次数上限
- `meal_banquet_cooldown_minutes` — 设宴冷却（分钟）

### 戳一戳互动

- `poke_reply_enabled` — 启用戳回复
- `poke_poke_back_chance` — 戳回概率 1~100
- `poke_complaint_texts` — 吐槽文案列表

### Debug 调试

- `debug_log_enabled` — 输出调试日志
- `memory_debug_enabled` — 记忆模块调试日志
- `engagement_debug_enabled` — 社交互动调试日志
- `affinity_debug_enabled` — 情感积分调试日志

---

## 数据存储

| 数据 | 存储方式 |
|------|---------|
| 用户画像 | 本地文件 |
| 会话总结 / 事件 | AstrBot 知识库 |
| 图片 caption cache / 审核 evidence | SQLite |
| 反思 / SAN / 好感度 | SQLite |
| Persona Sim | SQLite（`persona_state` / `persona_effects` / `persona_events` / `persona_todos` / `persona_episodes`） |
| 主动社交参与状态 | SQLite（`engagement_state` 表，含 unfinished_cues） |
| 表情包 | 本地目录 |

---

## 日志前缀

优先查看这些前缀：

- `[MemoryWrite]` / `[MemoryQuery]` / `[MemorySummary]` / `[MemoryStore]` / `[MemoryInject]`
- `[PersonaSim]` / `[PersonaDAO]` / `[Consolidation]`
- `[Engagement]` / `[OpportunityScore]` / `[ReplyIntent]` / `[ReplyExecutor]`
- `[Affinity]` / `[SAN]`
- `[Moderation]` / `[ModerationEnforcer]`
- `[FeedHandler]` / `[CaptionService]` / `[Poke]`

正常跳过通常只打 `debug`，不打 `warning`。

---

## 安装与迁移

1. 在 AstrBot 后台安装 `astrbot_plugin_self_evolution`
2. 创建基础知识库，把名称填入 `memory_kb_name`
3. 确保 AstrBot 已配置可用模型
4. 如启用图片审核，确认已配置图片理解 provider
5. 使用 NapCat 作为消息协议后端
6. 重载或重启 AstrBot

**迁移注意事项：**

- 重装不自动清空 AstrBot 知识库
- 重建数据库不删除画像文件和表情包目录
- scope 知识库按群聊 / 私聊自动隔离

彻底清理需分别处理：
- 数据库：`/db reset` 或 `/db rebuild`
- 表情包：`/sticker clear` 或删除本地表情包目录
- 画像：删除画像文件
- 知识库：在 AstrBot 知识库侧清理

---

## 代码入口

按优先级从这些文件开始阅读：

1. `main.py` — 插件主入口，命令注册，调度器初始化
2. `engine/eavesdropping.py` — 主动 / 被动社交入口
3. `engine/engagement_planner.py` — 场景分类与意图规划
4. `engine/opportunity_cache.py` — PendingOpportunity 缓存与评分
5. `engine/interject_preferences.py` — 用户主动互动偏好
6. `engine/reply_executor.py` — 回复执行
7. `engine/persona_sim_engine.py` — 人格状态引擎
8. `engine/persona_sim_rules.py` — Effect 触发规则
9. `engine/persona_sim_injection.py` — Prompt 注入逻辑
10. `engine/persona_sim_consolidation.py` — 日结逻辑
11. `engine/persona_sim_todo.py` — 脑内待办生成
12. `cognition/san.py` — SAN 精力分析
13. `dao.py` — SQLite 持久化
14. `scheduler/` — 定时任务编排
