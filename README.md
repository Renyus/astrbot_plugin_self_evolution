# 自我进化

`astrbot_plugin_self_evolution` 是 AstrBot 的长期能力增强插件，适配群聊、私聊和 NapCat 消息结构。

目标是让 Bot 在无对话时也在"变化"——有情绪起伏、有社交需求、有待办挂念、有记忆延续，而不是每次对话都是一张白纸。

---

## 功能架构

### 记忆与画像

维护用户画像（身份、偏好、特征、备注）和会话事件/每日总结，支持按群聊/私聊自动 scope 隔离。长期知识库可召回并注入 Prompt。

- `profile.py` — 用户画像增删改查
- `session_memory_store.py` — 会话事件持久化
- `memory_router.py` / `memory_query_service.py` — 记忆查询与注入
- `session_memory_summarizer.py` — 每日会话总结

### 社交参与（主动 / 被动）

Bot 根据场景（IDLE / CASUAL / HELP / DEBATE）和沉默时长判断是否要主动插话，同时处理被动回复请求。输出约束（OutputGuard）防止 Bot 自曝、说过长或重复内容。

- `eavesdropping.py` — 主动触发入口
- `engagement_planner.py` — 场景分类、意图规划
- `reply_executor.py` — 回复执行（文本 / emoji reaction / 表情包）
- `reply_policy.py` — 统一仲裁（cooldown / flood / wave 占用）
- `output_guard.py` — 输出安全约束

### Persona Sim 2.0 — 人格生活模拟

内置人格状态引擎，追踪能量、心情、社交需求、饱腹感，基于时间差推演状态变化，触发短期 Effect 和脑内待办，以极短片段注入 Prompt。详见下一节。

### 认知与情感

- `cognition/san.py` — SAN 精力值动态分析，基于群消息情绪波动调整
- `engine/affinity.py` — 情感积分，增减依赖互动质量
- `scheduler/` — 每日反思批处理、好感度恢复等定时任务

### 审核与娱乐

- `caption_service.py` / `moderation_classifier.py` — 图片 caption 生成 + NSFW / 引流推广二次推定
- `sticker_store.py` — 表情包学习与发送
- `meal_store.py` — 群菜单

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
