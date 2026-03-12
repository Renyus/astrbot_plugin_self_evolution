# 更新日志

本项目的所有重大更改都将记录在此文件中。格式基于 [Keep a Changelog](https://keepachangelog.com/)。

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

## [3.7.1] - 2026-03-10

- 修复 active_buffers 内存泄漏
- 移除重复的 return 语句
- 扩展元编程模块映射（支持 memory/profile/persona）

---

## [3.7.0] - 2026-03-09

- 删除 `engine/chat_logger.py`，复用 AstrBot 内置消息历史

---

## [3.6.0] - 2026-03-09

### 群聊记忆系统

- 以知识库为存储的群聊记忆写入和读取闭环
- 新增 `save_group_knowledge` LLM 工具
- 同时检索群公共记忆和用户个人记忆

---

## [3.5.1] - 2026-03-09

### 画像溯源系统

- 画像标签携带证据 UUID，可追溯来源
- 更新画像时自动记录触发消息的 UUID

---

## [3.5.0] - 2026-03-09

### 群聊日志与上下文追踪

- 新增 ChatLogger 组件，以 UUID 标识每条消息
- 异步非阻塞写入，按天分割，7 天自动轮转
- 支持检测用户引用 AI 消息并自动注入上下文

---

## [3.4.0] - 2026-03-09

### 用户画像系统

- 双轨触发：关键词命中 / @机器人 -> 开启缓存
- 滑动窗口冷场判定
- LLM 智能画像提取，带防欺骗过滤
- 权重衰减、定时清理
- 新增命令：`/view_profile`、`/delete_profile`、`/profile_stats`

---

## [3.3.2] - 2026-03-09

- 修复所有 LLM 工具 docstring 参数格式（使用 `Args:` 替代 `:param`）

---

## [3.3.1] - 2026-03-09

- 修复 `list_tools` 使用不存在的 API

---

## [3.3.0] - 2026-03-09

- 代码解耦：拆分 `engine/memory.py` 和 `engine/persona.py`

---

## [3.2.14] - 2026-03-08

- 记忆检索优化：私聊只检索用户画像，群聊同时检索群记忆和说话人画像

---

## [3.2.13] - 2026-03-08

- 记忆存储改用知识库按用户/群汇总存储
- 删除本地文件存储和 `read_chat_history` 工具

---

## [3.2.11] - 2026-03-08

- 新增自动记忆检索与注入，无需 LLM 手动调用工具

---

## [3.2.7] - 2026-03-08

### 对话幻觉修复与记忆优化

- 群聊消息标注说话者身份 `[群成员N]`
- 存入记忆时自动添加元信息
- 新增记忆去重和自动清理
- 新增工具：`learn_from_context`、`clear_all_memory`、`list_memories`、`delete_memory`、`auto_recall`
- 自动学习触发器

---

## [3.2.5] - 2026-03-08

- 参数全量热重载（dynamic property 模式，修改配置即时生效）

---

## [3.2.4] - 2026-03-08

- 发言意愿可配置（`interjection_desire` 1-10）
- 元评论硬过滤（拦截"监测报告式"回复）

---

## [3.2.0] - 2026-03-08

### CognitionCore 5.0 人设去中心化

- 移除所有硬编码人设字符串
- 支持通过配置面板自定义机器人名称、头衔、风格
- 所有系统指令升级为参数化模式

---

## [3.1.8] - 2026-03-08

### 意图预扫描拦截器

- 基于正则的关键词实时感应，命中时 0 延迟触发评估
- 新增 `force_immediate` 标志位，绕过消息缓冲区

---

## [3.1.6] - 2026-03-08

- 重构插嘴引擎提示词，降低服务商安全审查拦截概率
- 新增 Safety Check 类错误的专用捕捉和静默处理

---

## [3.1.5] - 2026-03-08

### 身份隔离与好感度修复

- 深度注入身份归因指令，防止群聊中"连坐"错误
- 引入每日好感度自动恢复机制（"大赦天下"）
- 新增 `/set_affinity` 管理员命令

---

## [3.1.0] - 2026-03-08

### 架构解耦与模块化

- 重构为 Python 包结构
- 拆分 `dao.py`、`engine/eavesdropping.py`、`engine/meta_infra.py`
- 元编程支持按模块操作
- `main.py` 缩减约 70%

---

## [3.0.0] - 2026-03-08

### 主动插嘴引擎

- 新增 `on_message` 钩子，实现主动环境监听
- `active_buffers` 消息池化 + 静默 LLM 判断
- 防内存溢出：`buffer_threshold`、`max_buffer_size` 暴露到配置面板
- 新增 `/affinity` 查询命令
- 实装 CC BY-NC 4.0 协议

---

## [2.3.0] - 2026-03-08

### CognitionCore 2.0

- 情感防火墙：`user_relationships` 情感矩阵表 + `update_affinity` 工具
- 毫秒级零消耗熔断：好感度归零时物理拦截
- 认知蒸馏：后台反思任务改为实体提取，避免向量库污染

---

## [2.2.1] - 2026-03-08

- 高级身份与语境追踪：群名片读取、动态权限识别、回复/At 目标解析

---

## [2.2.0] - 2026-03-08

- 群聊身份与语境感知：结构化获取发送者信息，解决多人对话中上下文混淆

---

## [2.1.1] - 2026-03-08

- 修复定时任务在无 Session 上下文时崩溃的问题
- 放宽 AST 安全校验，允许合理的插件代码使用 `os`、`sys` 等基础库

---

## [2.1.0] - 2026-03-08

- 新增管理员命令：`/reject_evolution`、`/clear_evolutions`、`/reflect`
- 新增 `validate_plugin.py` 预检测脚本

---

## [2.0.4] - 2026-03-07

- 修复自定义人格定位：深度集成 `resolve_selected_persona` 机制
- 修复 Aiocqhttp 平台兼容性

---

## [2.0.3] - 2026-03-07

- 修复全局管理员穿透判定
- 补齐 `_conf_schema.json` 中缺失的配置项
