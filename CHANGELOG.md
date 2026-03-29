# 更新日志

本文件记录项目中值得关注的功能变更、修复和文档调整。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

说明：

- `2.x` 是当前插件版本号体系
- 更早的 `5.x`、`4.x`、`3.x` 记录来自此前的内部阶段版本，保留用于历史参考

## [3.3]

### Added

- `/set_san [值]` 管理员命令：查看或手动设置当前精力值
- `engine/affinity.py` — 关系温度底盘：自动弱信号积分引擎
  - `direct_engagement`：@bot/回复bot/私聊 → +1，6小时冷却
  - `friendly_language`：礼貌词(谢谢/厉害/牛等) → +1，每天2次
  - `hostile_language`：攻击词(滚/傻/废物等) → -2，1小时冷却
  - `returning_user`：连续回访 → +1，每天1次
- `/affinity show` — 用户命令：查看当前好感度
- `/affinity debug <用户ID>` — 管理员命令：查看详细好感度状态与信号记录
- `engine/memory_types.py` — 统一记忆请求/结果数据结构
- `engine/memory_query_service.py` — 统一记忆查询中枢
- `engine/memory_tools.py` — LLM 工具与记忆主链适配层
- `engine/session_memory_store.py` — 会话总结/事件的 KB 存取层
- `engine/session_memory_summarizer.py` — 前一自然日消息抓取与每日总结层
- `engine/profile_store.py` — 用户画像存取与结构化变更层
- `engine/profile_builder.py` — 手动/自动画像构建层
- `engine/profile_summary_service.py` — 用户画像摘要生成层

### Changed

- `/view` 命令改为纯只读，不再隐式触发 LLM 刷新画像（副作用移至 `/update`）
- `get_user_messages` 工具改为真正的 `fetch_limit` 语义：群聊按目标用户消息条数精确翻页，不再硬截 20 条上限
- `scheduled_reflection` 与好感度恢复拆分为独立任务 `scheduled_affinity_recovery`，批处理失败不再偷偷恢复好感度
- `build_profile()` 和 `analyze_and_build_profiles()` 的 prompt 统一改为输出 `## identity / preferences / traits / recent_updates / long_term_notes` 结构化 Markdown
- `parse_target_user()` 改为识别 `/profile <subcommand> [user_id]` 命令组格式，不再错误把子命令当用户 ID
- 互动系统重构为社交行为引擎，支持 IGNORE/REACT/BRIEF/FULL 四级参与策略和 IDLE/CASUAL/HELP/DEBATE 群态识别（可通过 `engagement_new_system_enabled` 开关切换）
- 记忆系统重构为“统一写入路由 + 统一查询中枢 + 分层存储”架构：
  - 所有写入先经过 `MemoryRouter`
  - 所有读取先经过 `MemoryQueryService`
  - Prompt 注入与 LLM 工具共用同一套记忆查询规则
  - 人物记忆、会话事件、每日总结和反思边界进一步分离
- `main.py` 中的记忆相关工具逻辑下沉到 `MemoryTools`
- 会话总结链拆分为 `SessionMemoryStore` 与 `SessionMemorySummarizer`
- 用户画像链拆分为 `ProfileStore`、`ProfileBuilder` 与 `ProfileSummaryService`

### Fixed

- `identity_keywords` 移除 `"是"`, `"有"`, `"养"`, `"要"` 等过于通用的单字，避免普通句子误判为身份信息
- `process_message()` 新增 bot 自消息过滤，防止平台回环时给自己刷分
- `_is_command_message`（原 `_is_command_only`）真正识别命令型消息（前缀+命令词/中文），而非仅识别"只有前缀没有内容"的消息
- `avatar_url` 头像尺寸从 `s=256` 改为 `s=40`，避免图片传输失败
- **互动 extras 前置**：`main.py` 在 `on_message_listener` 中提前计算 `is_at`/`has_reply` 并写入 `event.set_extra()`，让 `affinity.py` 和 `eavesdropping.py` 共用同一来源，避免各模块重复解析
- **私聊不再误拦**：修复 `is_at_or_wake_command and not has_at_to_bot and not has_reply_to_bot` 条件缺少 `group_id` 判断，导致私聊普通消息被 early-return 拦截的问题
- **@all 不再误判为 direct_engagement**：`event_context.py` 中 `at_info` 判断从 `"all" in at_targets or bot_id in at_targets` 改为仅 `bot_id in at_targets`，符合 direct_engagement = @bot/回复bot/私聊 的定义
- **被动插嘴窗口状态不再因 early-return 丢失**：`eavesdropping.py` 在 `eligibility` 不通过或 `plan.level == IGNORE` 时，先构建并保存 `new_state`（含 `last_message_time`、`message_count_window`、`scene_type` 等）再返回，避免每条消息都重置为默认窗口值，导致 scene 永远停留 idle
- **scene 分类不再粘滞**：`engagement_planner.py` 的 `classify_scene_from_state()` 删除"非 CASUAL 直接返回旧 scene"的逻辑，改为每次按当前窗口字段重算；`eavesdropping.py` 读库时 scene 固定初始化为 CASUAL，由 planner 重新计算真实场景。连续消息现在能正确从 IDLE → CASUAL → REACT/BRIEF 流转
- **主动路径不再因过期窗口误触发**：在 `check_engagement()`（主动插嘴定时任务）中补上 `window_active` 判断，超过 `_MESSAGE_WINDOW_SECONDS`（120s）未活跃的群将把窗口计数清零，避免安静群被误判为 CASUAL 而触发插嘴
- **主动路径不再覆盖已有问句/情绪计数**：`check_engagement()` 中 `compute_scene_windows([], state)` 空消息列表不再覆盖 `question_count_window`/`emotion_count_window`，只更新 `mention_bot_recently`，保留 DAO 中已有积累
- **主动插嘴执行后回写冷却状态**：`check_engagement()` 在 `result.executed` 后补充 `save_engagement_state()`，更新 `last_bot_engagement_at`、`last_bot_engagement_level`、`consecutive_bot_replies`，避免连续调度周期重复主动发言
- **框架正常回复同步社交冷却**：`eavesdropping.py` 新增 `sync_framework_reply_state(scope_id, level)` 方法；`main.py` 在 `@filter.after_message_sent()` 钩子中调用它，普通对话发完后立即更新 `last_bot_engagement_at` 和 `consecutive_bot_replies`，避免 3 秒后又来主动插嘴；窗口内保留已有计数，窗口失效则清零
- **engagement 层级语义重构**：执行层 `REACT`=表情包（删文字模板），`BRIEF`→`FULL`（合并到 LLM 回复链路），`FULL`=保持现有 LLM 回复；`engagement_executor.py` 移除 `REACT_TEMPLATES`/`BRIEF_TEMPLATES`，`_execute_react()` 仅发表情包，`BRIEF` 在 `execute()` 中直接路由到 `_execute_full()`，`_execute_full()` LLM 失败降级改为发表情包；规划层所有 `BRIEF` 分支统一改为 `FULL`（IDLE/HELP/CASUAL 有唤醒均走 FULL，DEBATE 有唤醒也改为 FULL）
- **REACT 表情包链路接口修复**：`engagement_executor._try_send_sticker()` 之前调用的 `get_random_sticker`/`send_sticker_by_uuid` 在 entertainment 模块中不存在；`entertainment.py` 新增 `send_sticker_for_engagement(group_id)` 方法，复用 sticker_store 现成接口（get_random_sticker → get_sticker_path → base64 编码 → send_group_msg），调用 `should_send_sticker()` 检查冷却，冷却只在发送成功后更新，发送失败不占用冷却
- **FULL 回复复用主链路上下文**：`main.py` 新增 `generate_social_reply()` 公共方法，复用 `_build_identity_injection`、`_build_group_history_injection`、`_build_profile_injection`、`_build_kb_memory_injection`、`_build_behavior_hints` 等现有注入构建逻辑生成社交回复；任务说明直接包含触发消息文本；`_execute_full()` 重新补回 `interject_trigger_probability` 概率门；被动链路透传 `quoted_info`/`at_info` 到 `generate_social_reply()`，使 @/回复 触发时 identity 注入和 profile 判断能获取真实上下文
- **配置项 list 语义统一收口**：`config.py` 新增 `_get_nested_list()` 统一读取 list 类型配置；`target_scopes`、`sticker_target_qq`、`meal_eat_keywords`、`meal_banquet_keywords`、`surprise_boost_keywords`、`admin_users` 全部改走 list 语义；`_conf_schema.json` 中 `sticker_target_qq` 和 `surprise_boost_keywords` 从 string 改为 list；`_get_nested_list()` 同时兼容 `|` 和 `,` 分隔符，确保旧有逗号配置（如 `"123,456"`）平滑迁移

### Config

| 配置 | 说明 |
|------|------|
| `affinity_auto_enabled` | 总开关 |
| `affinity_direct_engagement_delta` | 主动互动加分 |
| `affinity_friendly_language_delta` | 礼貌语言加分 |
| `affinity_hostile_language_delta` | 攻击语言扣分 |
| `affinity_returning_user_delta` | 回访用户加分 |
| `affinity_direct_engagement_cooldown_minutes` | 互动冷却（分钟） |
| `affinity_friendly_daily_limit` | 礼貌词每日上限 |
| `affinity_hostile_cooldown_minutes` | 攻击冷却（分钟） |
| `affinity_returning_user_daily_limit` | 回访每日上限 |
| `affinity_recovery_enabled` | 每日好感度恢复开关 |
| `engagement_react_probability` | casual场景轻反应概率 |
| `engagement_new_system_enabled` | 启用新版社交行为引擎 |

### Tests

- 新增 `tests/test_affinity.py`（14 tests）
- 新增 `ParseTargetUserTests`（7 tests）
- 新增 `tests/test_engagement.py`（17 tests）
- **总计：231 tests**

### 2026-03-23 补充记录

- 新增 `/db rebuild` 管理员命令，用于删除插件数据库文件并重建空库
- `/db confirm` 现在会根据最近一次待确认动作执行 `reset` 或 `rebuild`
- 修复知识库智能检索误调用 `KBHelper.retrieve()` 的问题，改为走 `kb_manager.retrieve()`
- 补齐 `pending_evolutions`、`session_reflections`、`group_daily_reports` 的建表逻辑
- README 补充数据库维护说明与重装插件注意事项

- `dao.py` timeout 参数语法错误 `3.0.0` → `3.0`

## [3.0.0] - 2026-03-22

> 核心记忆系统 / 调度层 / 命令层全面薄编排化

### 架构变更

**核心模块（Core）**
- `engine/memory.py` — 重构为 3 层记忆架构（Reflection Hints / Structured Profile / Knowledge Base）
- `engine/memory_router.py` — 新增统一路由层，决定信息去往哪层记忆
- `engine/profile.py` — 新增 `StructuredProfile` dataclass，`classify_fact()` 优先级重构，长期笔记自动晋升
- `engine/reflection.py` — `distill_profile_facts()` 改用 memory_router，支持 session_event 分类
- `scheduler/tasks.py` — 拆分为薄编排层，统一 scope 发现、任务包装器、日志、异常处理
- `scheduler/register.py` — 重构为统一任务注册
- `commands/common.py` — 新增命令层公共基础设施（`CommandContext`、`ensure_admin`/`ensure_not_private_other`/`ensure_group`）
- `commands/profile.py` — 复用 `common.py`，权限校验集中
- `commands/admin.py` — 复用 `common.py`，权限和 scope 校验提取
- `commands/sticker.py` — 删除冗余定义，薄适配化
- `config.py` — 新增 `target_scopes`、`interject_trigger_probability`、`memory_fetch_page_size`、`memory_summary_chunk_size` 等配置键，`__getattr__` 收紧

**可选模块（Optional）**
- `engine/eavesdropping.py` — 拆解 `interject_check_group()` 为 5 层薄编排，新增状态机结构

### 配置变更

**新增配置**

| 配置 | 说明 |
|------|------|
| `enable_profile_injection` | 是否向提示词注入画像摘要 |
| `enable_profile_fact_writeback` | 是否将反思事实写回画像 |
| `enable_kb_memory_recall` | 是否召回知识库记忆 |
| `memory_fetch_page_size` | 历史消息分页大小 |
| `memory_summary_chunk_size` | LLM 分段 chunk 大小 |
| `interject_trigger_probability` | 主动插嘴触发概率 |
| `target_scopes` | 白名单目标 scope 列表 |

**行为修正**

- `interject_random_bypass_rate` 运行时默认值已与 schema 统一（0.5）
- `interject_trigger_probability` 替换遗留的 `interject_random_bypass_rate`
- `__getattr__` 对未知 key 抛出 `AttributeError` 而非返回 `None`

### Bug Fixes

- `save_session_event()` 同日前缀重复写入 bug → 移除错误删除逻辑
- KB retrieval 私聊场景 group_id 强制要求 bug → 移除该检查
- `long_term_notes` 未计入 `total_items` budget bug → 已计入
- `classify()` explicit fact_type 被启发式覆盖 bug → 优先级已修正
- `get_structured_summary()` `recent_updates` 输出后未扣减 remaining → 已修正
- 白名单路径未按 `include_private`/`include_groups` 过滤 scope bug → 已修正
- `handle_delete()` 缺少普通用户权限校验 P1 → 已补全
- `message_normalization.py` Image import 在无环境时失败 → try/except fallback

### 测试

- 新增 `test_memory_router.py`（16 tests）
- 新增 `test_main_prompt_injection.py`（18 tests）
- 新增 `test_eavesdropping.py` 辅助函数测试（14 tests）
- 新增 `test_scheduler.py` 基础设施测试（20 tests）
- 新增 `test_admin_commands.py`（14 tests）
- 新增 `test_sticker_commands.py`（11 tests）
- 补全 `test_profile_commands.py` P1 权限回归测试

**总计：166 tests**

## [2.8.8] - 2026-03-21

### Added

- 新增 `sticker_send_threshold` 配置项，控制表情包发送概率阈值

### Changed

- `interject_random_bypass_rate` 改为控制插嘴最终触发概率，LLM判定满足条件后以此概率决定是否执行

### Removed

- 完全移除表情包打标签功能及相关逻辑

## [2.8.7] - 2026-03-21

### Changed

- 表情包学习增加 sub_type 判断，sub_type=0 的普通图片不再学习
- 新增频率判定：sub_type=0 时根据同一图片被发送次数判断是否为表情包

### Removed

- 完全移除表情包打标签功能及相关逻辑
- 移除 `sticker_tag_cooldown` 配置项
- 移除 `tags` 和 `description` 字段，简化存储结构

### Added

- `sticker_freq_threshold` 配置项：控制频率判定阈值

## [2.8.6] - 2026-03-20

### Changed

- 统一 `target_group_scopes` 配置项，移除重复的 `interject_whitelist`，现主动插嘴、记忆、画像构建、SAN 值分析均使用同一目标群列表。

## [2.8.5] - 2026-03-19

### Added

- 打通私聊画像、私聊会话总结、私聊批处理和私聊历史消息读取。
- 为主动插嘴增加 `interject_require_at` 配置项，用于控制是否要求最新消息必须 `@` 机器人。
- 新增配置契约测试与多组回归测试，覆盖画像、NapCat 历史消息适配、调度、总结和知识库隔离逻辑。

### Changed

- 会话总结不再继续默认混写到同一个知识库，而是按群聊或私聊 scope 自动隔离存储。
- 当前会话会自动绑定到自己的 scope 知识库，以便 AstrBot 的知识库召回优先命中本会话总结。
- 每日会话总结与每日批处理都改为严格按“前一自然日”取消息，不再简单截取最近若干条历史记录。
- README 全量重写，统一为当前行为说明，补充了知识库、私聊支持、后台任务和修复后的行为变化。
- 文案和命名统一向“会话”语义靠拢，减少“群聊总结”“群日报”等旧说法带来的歧义。

### Fixed

- 统一 NapCat 历史消息发送者读取方式，优先按 `sender.user_id` 处理。
- 修复自动画像和日报处理中直接消费原始消息结构的问题。
- 修复画像文件命名依赖昵称导致的旧档误读问题，改为稳定命名并兼容迁移旧文件。
- 修复好感度 `reset` 和每日恢复后的缓存脏数据问题。
- 修复 `/sticker untagged` 缺少 `created_at` 字段导致的异常。
- 修复定时任务在无活跃缓存时空跑的问题。
- 修复私聊每日任务依赖短时活跃窗口导致重启后目标丢失的问题，改为持久化已知私聊 scope。
- 修复会话总结清理逻辑，避免默认清空整个知识库。
- 修复画像工具和反思事实回写会破坏 YAML 元数据结构的问题。
- 修复自动画像和每日画像刷新会把 bot 自己当成目标用户的问题。
- 修复若干 README、Schema 与运行时行为不一致的问题。

### Removed

- 删除多处确认无引用的死代码和无效配置项。
- 移除 `_post_init`、`_current_boredom_state` 等无实际消费的遗留逻辑。

### Tests

- 扩展为 60 条回归测试，覆盖画像、反思、总结、知识库隔离、调度、消息归一化和配置契约。

## [2.7.0] - 2026-03-17

### Changed

- 重构 NapCat 相关消息解析链路，提取共享函数 `parse_message_chain()` 和 `get_group_history()`。
- 新增 `disable_framework_contexts` 与 `inject_group_history` 配置项。
- 统一多处 Prompt 注入与消息解析逻辑。

### Fixed

- 修复画像 YAML 中带 Markdown 代码块时的解析问题。
- 修复 persona 和 identity 的重复注入问题。
- 修复主动插嘴模块中的消息顺序、`@` 检测、新增消息计算、冷却期处理与引用消息解析问题。
- 修复内存总结模块中的消息顺序与日志缺失问题。
- 修复 SAN 系统中 `None + int` 的异常。

### Performance

- 为会话窗口、插嘴状态和漏斗积分器补充并发保护。

## [2.5.0] - 2026-03-16

### Added

- 接入 MCP 工具能力，包括网页搜索与图像识别等能力。
- 新增插嘴成功后的活跃用户画像分析与自动构建。
- 新增“感兴趣用户”标记逻辑。

### Changed

- 优化画像总结提示词，去掉过度简化的长度限制。
- 优化插嘴冷静期逻辑，在消息不足且无人 `@` 或回复时不重复插嘴。

### Fixed

- 修复 NapCat API 获取机器人 QQ 号的问题。
- 修复 MCP 工具与内置工具同名时的冲突问题。

## [1.0.0] - 2026-03-14

### Added

- 新增表情包学习、表情包全局共享与 UUID 管理。
- 新增内心独白缓存机制。
- 新增 `/db show`、`/db stats`、`/db reset` 等数据库管理命令。

### Changed

- 表情包列表改为展示 UUID 而非自增 ID。
- `/db show` 用于查看数据库统计。

### Fixed

- 修复贤者时间冷却机制中的欲望指数衰减问题。
- 修复正负数判定逻辑。
- 修复 LLM 返回 `0` 时的变量未定义问题。
- 修复旧数据库自动迁移 UUID 列的问题。
- 修复 `send_sticker` 工具返回值告警。

### Removed

- 移除 `reindex` 命令。
- 移除表情包相关 emoji 文案。

## 历史阶段版本

以下记录保留原始版本编号，仅作历史参考。

## [5.5.1] - 2026-03-13

### Changed

- 与 AstrBot 框架进一步解耦，移除与框架 LongTermMemory 和知识库冲突的旧逻辑。
- 删除 `periodic_check`，合并到新的会话管理链路。
- 添加 `debug_log_enabled` 与 `max_prompt_injection_length` 配置项。

### Fixed

- 修复信息熵判断逻辑反转问题。
- 修复 Prompt 注入长度控制中的空值异常。

## [5.2.0] - 2026-03-13

### Changed

- 新增 `ImageCacheEngine`，统一图像描述缓存。
- 统一 engine 和 cognition 模块通过 `self.plugin.cfg` 访问配置。
- 新增 `engine/__init__.py` 统一模块导出。

### Added

- 图片作为发言意愿加分项。
- 纯图片消息参与互动意愿计算。

## [5.1.1] - 2026-03-12

### Fixed

- 修复多个切片前未检查空字符串的问题。
- 修复 `interest_boost` 返回类型错误。
- 提取 `bucket_data` 初始化为私有方法。

## [5.1.0] - 2026-03-11

### Changed

- 删除 `active_buffers`，统一使用 `session_buffers`。
- 引入“有趣/无聊”动态判定机制。
- 统一人格设定注入顺序。

### Added

- 新增 `eavesdrop_threshold_min` 与 `eavesdrop_threshold_max`。
- 添加触发计数器以避免无限重复判定。
- 引入 session 超时批量存储逻辑，减少知识库碎片。

### Fixed

- 修复 session 超时清理失效问题。
- 修复 `_dream_group_summary` 缺少 `await`。
- 修复画像截断方向，改为保留最新内容。
- 为 `clear_all_memory` 和 `update_affinity` 增加更多安全约束。

## [5.0.16] - 2026-03-11

### Added

- 模块化会话管理，新增 `engine/session.py`。
- 支持基于时间和消息数量的定时互动意愿检查。
- 新增 `session_whitelist`、`session_max_tokens`、`eavesdrop_interval_minutes` 等配置项。

### Fixed

- 修复定时任务持久化与 handler 丢失重建问题。
- 修复配置代理访问错误和若干冗余逻辑残留。

## [5.0.15] - 2026-03-10

### Added

- 中间消息过滤器，用于拦截工具调用期间的过渡性回复。

### Changed

- 增强多个模块的缓存清理逻辑。
- 重写旧版 `DOCUMENTATION.md`。

### Fixed

- 修复 4 处裸 `except`。

## [4.2.0] - 2026-03-10

### Added

- 新增主动无聊机制。
- 新增多智能体审查的 JSON 配置能力。
- 新增跨群知识关联分析。

## [4.1.0] - 2026-03-10

### Added

- 引入情绪依存记忆。
- 引入内心独白机制。
- 引入记忆模糊化表达。

## [4.0.1] - 2026-03-10

### Added

- 新增惊奇驱动学习。
- 新增关系图谱 RAG 和相关命令。

## [4.0.0] - 2026-03-10

### Added

- 引入多智能体对抗审查机制。

## [3.9.0] - 2026-03-10

### Added

- 引入分层失活机制。
- 引入泄漏积分器。
- 引入突发偏好检测。

### Fixed

- 修复私聊误触发插嘴与 IGNORE 误回复问题。
- 修复 `user_id` 类型不一致问题。

## [3.8.0] - 2026-03-10

### Changed

- 将 LLM 密集型工作从实时交互转移到批处理。
- 画像存储从复杂 JSON 改为 Markdown 文本块。
- 移除旧的实时向量检索注入逻辑。

### Added

- 新增“做梦”机制和一批夜间批处理配置。

## 更早版本

更早的细节请直接查看 Git 提交历史。
