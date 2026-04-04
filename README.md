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

### 社交参与（主动 / 被动）

Bot 根据场景（IDLE / CASUAL / HELP / DEBATE）和沉默时长判断是否要主动插话，同时处理被动回复请求。

#### Prompt 注入

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

#### 相关文件

- `engine/eavesdropping.py` — 主动触发入口
- `engine/engagement_planner.py` — 场景分类、意图规划
- `engine/reply_executor.py` — 回复执行
- `engine/reply_policy.py` — 统一仲裁
- `engine/output_guard.py` — 输出安全约束

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

插件可监控指定 GitHub 仓库指定分支，当检测到新 commit 时向目标群发送通知。

### 配置项

- `update_notify_enabled` — 启用 GitHub 更新通知
- `update_notify_repo` — 仓库路径，格式 `owner/repo`
- `update_notify_branch` — 分支名，默认 `main`
- `update_notify_group_ids` — 接收通知的群 ID 列表（支持多群）
- `update_notify_schedule` — 检查 cron 表达式，默认每 30 分钟

### 首次运行保护

首次检测到 commit 时不会发送通知（防止首次运行推送大量历史 commit），之后仅在新 commit 出现时通知。使用 `/db github_reset` 可重置缓存，强制重新检测。

---

## 命令参考

### 用户命令

| 命令 | 说明 |
|------|------|
| `/se help` | 查看指令帮助 |
| `/reflect` | 触发反思 |
| `/af show` | 查看好感度 |
| `/san show` | 查看 SAN 状态 |
| `/profile view [用户]` | 查看画像 |
| `/profile create [用户]` | 创建画像 |
| `/profile update [用户]` | 更新画像 |
| `/addmeal <菜名>` | 添加菜品到群菜单 |
| `/delmeal <菜名>` | 删除菜品 |
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
| `/ps tick [群] [quality]` | 手动推进人格时间（none/negative/positive） |
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
- `debug_log_enabled` — 调试日志

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
- `profile_msg_count` — 画像消息阈值
- `profile_cooldown_minutes` — 画像冷却（分钟）
- `auto_profile_enabled` — 自动构建画像
- `auto_profile_schedule` — 画像构建计划
- `auto_profile_batch_size` — 每批处理群数
- `auto_profile_batch_interval` — 批次间隔（分钟）

### 行为与互动

- `affinity_auto_enabled` — 自动好感度更新
- `affinity_recovery_enabled` — 好感度每日恢复
- `interject_interval` — 插话检查间隔（分钟）
- `interject_cooldown` — 插话冷却（秒）
- `interject_trigger_probability` — 触发概率
- `engagement_react_probability` — emoji reaction 概率
- `san_auto_analyze_enabled` — 自动 SAN 分析

### 审核（moderation）

- `moderation.moderation_enabled` — 启用审核
- `moderation.moderation_enforcement_enabled` — 执行处罚
- `moderation.moderation_nsfw_keywords` — NSFW 关键词
- `moderation.moderation_promo_keywords` — 引流推广关键词
- `moderation.moderation_refusal_keywords` — 模型拒绝描述关键词
- `moderation.moderation_nsfw_refusal_confidence` — NSFW 置信度
- `moderation.moderation_promo_refusal_confidence` — 引流置信度
- `moderation.moderation_weak_keyword_confidence` — 关键词置信度
- `moderation.moderation_confidence_threshold` — 置信度门槛
- `moderation.moderation_escalation_threshold` — 踢人阈值
- `moderation.moderation_ban_duration_minutes` — 禁言时长
- `moderation.moderation_nsfw_warning_message` — NSFW 警告消息
- `moderation.moderation_promo_warning_message` — 引流警告消息

### 表情包与娱乐

- `sticker_learning_enabled` — 学习表情包
- `sticker_freq_threshold` — 表情包频率阈值
- `sticker_total_limit` — 表情包总数上限
- `sticker_reply_enabled` — 回复附加表情包
- `sticker_reply_chance` — 触发概率（1~100）
- `sticker_reply_max_per_hour` — 每小时上限
- `sticker_reply_min_text_length` — 最低文字长度
- `meal_max_items` — 菜单最大条目
- `meal_eat_keywords` — 吃饭关键词
- `meal_banquet_keywords` — 设宴关键词
- `meal_banquet_count` — 设宴数量
- `meal_banquet_cooldown_minutes` — 设宴冷却

---

## 数据存储

| 数据 | 存储方式 |
|------|---------|
| 用户画像 | 本地文件 |
| 会话总结 / 事件 | AstrBot 知识库 |
| 图片 caption cache / 审核 evidence | SQLite |
| 反思 / SAN / 好感度 | SQLite |
| Persona Sim | SQLite（`persona_state` / `persona_effects` / `persona_events` / `persona_todos` / `persona_episodes`） |
| 表情包 | 本地目录 |

---

## 日志前缀

优先查看这些前缀：

- `[MemoryWrite]` / `[MemoryQuery]` / `[MemorySummary]` / `[MemoryStore]` / `[MemoryInject]`
- `[PersonaSim]` / `[PersonaDAO]` / `[Consolidation]`
- `[Engagement]` / `[ReplyIntent]` / `[ReplyExecutor]`
- `[Affinity]` / `[SAN]`
- `[Moderation]` / `[ModerationEnforcer]`
- `[FeedHandler]` / `[CaptionService]`

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
4. `engine/reply_executor.py` — 回复执行
5. `engine/persona_sim_engine.py` — 人格状态引擎
6. `engine/persona_sim_rules.py` — Effect 触发规则
7. `engine/persona_sim_injection.py` — Prompt 注入逻辑
8. `engine/persona_sim_consolidation.py` — 日结逻辑
9. `engine/persona_sim_todo.py` — 脑内待办生成
10. `cognition/san.py` — SAN 精力分析
11. `dao.py` — SQLite 持久化
12. `scheduler/` — 定时任务编排
