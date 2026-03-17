# 更新日志

本项目的所有重大更改都将记录在此文件中。格式基于 [Keep a Changelog](https://keepachangelog.com/)。

---

## [Release Ver2.5.1] - 2026-03-17

### NapCat API 整合修复

- **修复 Profile YAML 解析**: LLM 返回的 YAML 带有 Markdown 代码块，添加清理逻辑
- **修复重复注入**: 移除 persona 和 identity 的重复注入
- **新增配置项**: `disable_framework_contexts` 和 `inject_group_history`
- **共享函数提取**: 创建 `parse_message_chain()` 和 `get_group_history()` 统一消息解析

### 主动插嘴模块修复

- **修复消息顺序**: `get_group_msg_history` 返回倒序列表（最新在前），使用 `messages[-1]` 获取最新消息
- **修复 @ 检测**: 从 `comp.get("data", {}).get("qq")` 获取 QQ 号
- **修复新增消息计算**: 使用 `message_seq` 而非 `message_id` 追踪消息
- **修复冷却期**: 每次检查时更新 `last_msg_seq`，确保冷却期后正确计算新增消息
- **修复插嘴后重复触发**: 插嘴成功后正确更新 `last_msg_seq`
- **修复引用消息解析**: 调用 `get_msg` API 获取原文

### 内存模块修复

- **使用共享解析函数**: `parse_message_chain()` 替代手动解析
- **修复消息顺序**: 取最新的消息进行总结
- **添加日志**: 显示消息数量

### 性能优化

- **并发安全**: 添加 `_session_lock` 和 `_interject_lock`，使用 `_bucket_lock` 保护 `leaky_bucket`
- **代码格式**: 整理 `import` 语句到文件顶部

### Bug 修复

- **SAN 系统**: 添加防御性检查，避免 `None + int` 类型错误

---

## [Release Ver2.5.1] - 2026-03-16

### 新功能

- MCP 工具集成：新增 web_search 图像识别等 MCP 工具支持
- 自动分析用户画像：插嘴成功后自动分析群活跃用户并构建画像
- 画像感兴趣标记：LLM 判断用户是否值得"感兴趣"，画像中添加标记

### 优化

- 画像总结提示词：更详细的要求，不再限制 200 字
- 插嘴冷静期逻辑：bot 回复后，新消息不足 10 条且无人 @/回复时不再插嘴

### Bug 修复

- 修复 NapCat API 获取机器人 QQ 号问题
- 修复 MCP 工具与内置工具同名冲突

---

## [Release Ver1.0] - 2026-03-14

### 新功能

- 表情包学习：自动学习指定QQ用户的表情包，AI主动发送活跃气氛
- 表情包全局共享：所有群使用统一表情包库
- UUID管理：表情包使用UUID而非自增ID
- 内心独白：当AI判定"忽略"或"无聊"时生成内心独白缓存
- 数据库管理：/db show/stats/reset命令

### Bug 修复

- 修复贤者时间冷却机制：冷却期间欲望指数衰减
- 修复+1/-1判定逻辑：正确识别无方括号的正负数
- 修复LLM返回0时的变量未定义问题
- 修复UUID迁移：旧数据库自动添加uuid列
- 修复send_sticker工具返回值消除WARN
- 表情包列表显示UUID而非数字ID

### 优化

- 移除reindex命令（使用UUID无需重排）
- 移除表情包相关emoji符号
- /db show命令查看数据库统计

---

## [5.5.1] - 2026-03-13

### 与框架解耦

- 移除滑动窗口 prompt 注入（与框架 LongTermMemory 冲突）
- 移除自动记忆检索注入（与框架 KB 冲突）
- 移除 SessionManager 的 KB 存储逻辑
- 移除定时任务 periodic_check（合并到 SessionManager）

### 线程安全优化

- EavesdroppingEngine 添加 asyncio 锁（_bucket_lock, _boredom_lock, _active_users_lock, _intercepted_lock）
- SessionManager 添加 asyncio 锁（_buffer_lock）

### 信息熵修复

- 修复熵值判断逻辑反转问题（之前是高熵跳过，应为低熵跳过）
- 完善信息质量多维度检查：熵值、字符多样性、疑似乱码

### 图片处理优化

- 区分已知图片（有缓存）和未知图片（无缓存），注入不同引导语
- 添加 prompt 引导，告诉 LLM 不需要重复调用图像理解工具
- 优化性能：漏斗中只记录图片标记（boost=0.1），不调用图片处理

### 配置优化

- 删除多余配置项（boredom_threshold, session_auto_commit, session_commit_threshold 等）
- 硬编码信息熵阈值（0.3）到代码中
- 添加 debug_log_enabled 配置项
- 添加 max_prompt_injection_length 配置项

### Bug 修复

- 修复 prompt 注入长度控制报错（添加 None 检查）

---

## [5.2.0] - 2026-03-13

### 代码结构优化

- 新增 `ImageCacheEngine` 模块，统一管理图像描述缓存
- 统一所有 engine/cognition 模块使用 `self.plugin.cfg` 访问配置
- 新增 `engine/__init__.py` 统一导出模块

### 互动意愿优化

- 图片作为发言意愿加分项：检测到图片时增加欲望值
- 图片可单独触发发言意愿，也可作为关键词/@的额外加分项
- 纯图片消息不再被消息过短逻辑跳过

---

## [5.1.1] - 2026-03-12

### 代码审计修复

- 修复切片前未检查空字符串问题（6处）
- 修复 `interest_boost` 返回类型（int → float）
- 提取 `bucket_data` 初始化为私有方法 `_get_or_init_bucket_data`

---

## [5.1.0] - 2026-03-11

### 互动意愿机制优化

- 删除 `active_buffers`，统一使用 `session_buffers` 作为上下文数据源
- 新增有趣/无聊动态判定机制：LLM 在评估互动意愿时判断当前对话的兴趣值
  - 有趣判定：降低触发阈值 + 增加泄漏积分器欲望值
  - 无聊判定：提高触发阈值 + 降低 SAN 精力值
- 新增配置项 `eavesdrop_threshold_min`（默认 10）、`eavesdrop_threshold_max`（默认 50）
- 添加触发计数器，触发后重置以避免无限重复判定

### 会话管理修复

- 修复 session 超时清理的 5 分钟节流问题（此前导致会话永不超时）
- 将 `cleanup_stale` 改为 async 方法
- 修复存储顺序：先存入知识库成功后再删除缓冲
- 修复时间戳获取：使用 `time.time()` 替代 `asyncio.get_event_loop().time()`

### 记忆系统简化

- 删除旧的单条记忆存入逻辑（`auto_learn_trigger`、`_learn_to_memory`）
- 统一使用 session 超时批量存入（`session_*.txt` 格式），减少知识库碎片化
- 新增配置项 `session_auto_commit`、`session_commit_threshold`

### 审计修复

- 修复 `_dream_group_summary` 中缺少 `await` 的问题
- 修复画像截断方向（改为保留最新内容）
- 为 `clear_all_memory` 添加管理员权限检查
- 为 `update_affinity` 添加 delta 上限（MAX_DELTA = 20）
- 修复 `surprise_boost_keywords` 分隔符不一致问题
- 统一 `config.py` 与 `_conf_schema.json` 的默认值

### 其他

- 统一人格设定注入顺序：框架人格放在最前优先
- 修复 `get_user_messages` 工具描述中不支持的参数
- `source_uuids` 参数改为可选

---

## [5.0.16] - 2026-03-11

### 会话管理模块化

- 新增 `engine/session.py`，封装滑动上下文窗口管理逻辑（4k Token 维护）
- SessionManager 支持定时互动意愿检查（默认每 10 分钟或消息超过 20 条）
- 定时检查复用 EavesdroppingEngine 完整评估逻辑
- 配置参数：`session_whitelist`、`session_max_tokens`、`eavesdrop_interval_minutes`、`eavesdrop_message_threshold`

### 定时任务修复

- 修复 `persistent=True` 确保重启后定时任务恢复
- 修复 handler 丢失时的重新注册逻辑
- 为元编程工具添加 `PermissionType.ADMIN` 权限装饰器

### Bug 修复

- 修复配置访问方式，使用 `self.xxx` 代理而非 `getattr`
- 修复 `config.py` 属性拼写错误（eavesdrip -> eavesdrop）
- 清理冗余代码（active_buffers、_cleanup_stale_buffers 等）
- 修复 active_buffers 类型检查，防止类型错误导致异常

---

## [5.0.15] - 2026-03-10

### 中间消息过滤器

- 拦截工具调用期间的过渡性消息（如"让我先查查..."、"我来帮你..."）
- 使用正则模式匹配，在消息发送前过滤
- 新增事件钩子 `on_decorating_result`

### 缓存泄漏修复

- ProfileManager：添加过期缓存清理（每 5 分钟清理 5 分钟未访问的缓存）
- GroupVibeSystem：添加过期群组数据清理（每小时清理 24 小时无活动的群组）
- EavesdroppingEngine：增强清理逻辑，每 100 条消息同时清理多种缓存

### 其他

- 中间消息正则模式和 AI 意图正则模式预编译
- 修复 4 处裸 `except:` 语句
- 重写 DOCUMENTATION.md

---

## [4.2.0] - 2026-03-10

### 主动无聊机制

- 基于 zlib 压缩比的信息熵检测
- 连续低信息量消息累积无聊值
- 被@时可输出傲慢拒绝回复
- 配置项：`boredom_enabled`、`boredom_threshold`、`boredom_consecutive_count`、`boredom_sarcastic_reply`

### 可配置多智能体审查

- 审查智能体支持 JSON 格式配置
- 默认包含螺丝咕姆和阮梅两个审查 Agent
- 配置项：`debate_agents`

### 跨群知识关联

- 做梦任务新增跨群知识关联分析
- 寻找跨领域知识连接点，生成后续可引用的洞察

---

## [4.1.0] - 2026-03-10

### 情绪依存记忆

- 根据好感度动态调整记忆检索倾向
- 高好感度用户：关注共同兴趣和愉快记忆
- 低好感度用户：注意其过往的问题行为
- 零好感度用户：翻旧账、无情嘲讽

### 内心独白

- AI 在判定 IGNORE 时强制输出内心独白（`<inner_monologue>` 标签）
- 独白缓存到下次真正发言时注入
- 配置项：`inner_monologue_enabled`

### 记忆模糊化

- 画像生成时要求 LLM 标注置信度
- 低置信度记忆使用"我隐约记得..."、"似乎..."等不确定语气

---

## [4.0.1] - 2026-03-10

### 惊奇驱动学习

- 检测用户认知颠覆/惊喜表达（"我错了"、"原来如此"等关键词）
- 触发即时画像更新，弥补批量模式时效空窗
- 配置项：`surprise_enabled`、`surprise_boost_keywords`

### 关系图谱 RAG

- 记录用户在群聊中的互动关系
- 追踪用户活跃群组和频繁互动用户
- 提供关系图谱增强的记忆检索
- 新增命令 `/graph_info`、`/graph_stats`
- 新增模块 `engine/graph.py`
- 配置项：`graph_enabled`

---

## [4.0.0] - 2026-03-10

### 多智能体对抗

- 引入 GAN 风格的对抗审查机制
- 主控 Agent 生成代码提案，审查 Agent 进行对抗辩论
- 多轮辩论达成共识后才进入人工审核
- 配置项：`debate_enabled`、`debate_rounds`、`debate_system_prompt`、`debate_criteria`

---

## [3.9.0] - 2026-03-10

### 分层失活

- 核心信息永不丢失，边缘信息 10-20% 随机屏蔽
- 配置项：`dropout_enabled`、`dropout_edge_rate`、`core_info_keywords`

### 泄漏积分器

- 公式：`Z_t = Z_{t-1} * decay + boost`
- 替代死板的消息计数器，支持时间衰减
- 配置项：`leaky_integrator_enabled`、`leaky_decay_factor`、`leaky_trigger_threshold`、`interest_boost`、`daily_chat_boost`

### 突发偏好检测

- 实时检测用户偏好变化，提示 AI 主动调用 `update_user_profile`

### 其他

- 私聊不触发插嘴逻辑
- 修复 IGNORE 被错误回复的问题
- 修复 user_id 类型不一致问题
- 所有硬编码 Prompt 提取为配置项

---

## [3.8.0] - 2026-03-10

### 认知卸载

核心思想：将 LLM 密集型工作从实时交互转移到凌晨批量处理。

- 画像存储从复杂 JSON 改为 Markdown 文本块
- 新增"做梦"机制：凌晨批量处理用户画像、群记忆总结
- 移除实时向量检索（auto_recall_inject），改为 AI 主动调用
- 简化插嘴评估 Prompt，减少 Token 消耗
- 配置项：`profile_precision_mode`、`dream_enabled`、`dream_schedule`、`dream_max_users`、`dream_concurrency`

---

## [更早版本]

历史版本的更新日志请参考 Git 提交历史。
