# Self-Evolution -- AstrBot 认知增强插件

**版本**: 5.1.1 | **内核**: CognitionCore 6.0 | **协议**: CC BY-NC 4.0

交流群: 1087272376

---

## 概述

Self-Evolution 是一个面向 AstrBot 平台的认知增强插件。它赋予 AI 主动环境感知、长期记忆、用户画像、情感建模和自主互动意愿等能力，让 AI 从被动的问答工具升级为具备持续"生命感"的智能体。

插件的核心设计思想是"白天快速响应，夜间批量整理"：实时交互只做轻量级的关键词匹配和画像读取，LLM 密集型的画像构建和群记忆总结等工作推迟到凌晨的"做梦"任务中完成。

---

## 功能模块

| 模块 | 说明 | 默认 |
|------|------|------|
| 滑动上下文窗口 | 按 Token 预算维护群聊消息，注入 LLM 上下文 | 启用 |
| 主动互动意愿引擎 | 基于泄漏积分器的自然互动节奏，支持有趣/无聊动态调节 | 启用 |
| 用户画像系统 | Markdown 文本存储，支持分层失活、记忆模糊化、情绪依存记忆 | 启用 |
| 长期记忆 | 基于知识库的向量存储/检索，支持去重和自动清理 | 启用 |
| 做梦机制 | 凌晨批量构建画像、总结群记忆、跨群知识关联 | 启用 |
| 情感矩阵 | 0-100 好感度评分，低分熔断拦截 | 启用 |
| SAN 精力值 | 心智疲劳模拟，精力耗尽时拒绝服务 | 启用 |
| 群体情绪共染 | 感知群氛围，影响回复风格 | 启用 |
| 关系图谱 | 记录用户互动关系 | 启用 |
| 人格进化 | LLM 自主修改系统提示词，支持管理员审核 | 启用 |
| 元编程 | AI 读取/修改自身源码，多智能体对抗审查 | 关闭 |

---

## 环境要求

- AstrBot v4.19.2 或更高版本
- 至少一个已配置的 LLM Provider
- 一个名为 `self_evolution_memory` 的知识库（需在 AstrBot 后台手动创建）

---

## 安装步骤

1. 在 AstrBot 后台插件市场中搜索 `astrbot_plugin_self_evolution` 并安装
2. 在 AstrBot 后台创建知识库，名称设为 `self_evolution_memory`（可通过配置项 `memory_kb_name` 更改名称）
3. 根据需要调整插件配置
4. 重启 AstrBot 或热重载插件

---

## 项目结构

```
astrbot_plugin_self_evolution/
|-- main.py                 入口文件，生命周期管理，LLM 工具注册
|-- config.py               配置属性代理，所有配置项集中定义
|-- dao.py                  SQLite 数据访问层（WAL 模式，长连接池，读写锁分离）
|-- prompts.py              提示词管理器，支持从 YAML 加载
|-- prompts.yaml            全部提示词配置文件
|-- _conf_schema.json       AstrBot 配置面板 Schema
|-- metadata.yaml           插件元信息
|-- cognition/
|   |-- __init__.py
|   |-- san.py              SAN 精力值系统
|   +-- vibe.py             群体情绪共染系统
+-- engine/
    |-- session.py           滑动上下文窗口管理
    |-- eavesdropping.py     主动互动意愿引擎（漏斗机制 + 泄漏积分器）
    |-- memory.py            长期记忆管理（存储 / 检索 / 去重 / 清理）
    |-- profile.py           用户画像管理（Markdown 格式，支持缓存）
    |-- persona.py           人格进化管理（审核队列）
    |-- meta_infra.py        元编程基础设施（AST 校验 + 多智能体对抗辩论）
    +-- graph.py             关系图谱 RAG
```

---

## 消息处理流程

### 被动监听流程 (on_message_listener)

每条群聊消息到达时依次执行：

1. 写入滑动窗口 -- SessionManager 按 Token 预算维护消息队列
2. 记录关系图谱 -- GraphRAG 记录用户在群中的互动
3. 互动意愿评估 -- EavesdroppingEngine 进行多级过滤和决策

### 主动拦截流程 (on_llm_request)

当用户直接与 AI 交互（@、命令、私聊）时：

1. SAN 精力值检查 -- 精力耗尽则拒绝服务
2. 好感度检查 -- 好感度为零则物理熔断，拒绝处理
3. 动态上下文路由 -- 根据消息内容按需加载画像、图谱、偏好检测等模块
4. System Prompt 注入 -- 依次注入人格设定、身份信息、画像、关系图谱、SAN 状态、群氛围、核心准则、滑动窗口上下文

### 中间消息过滤 (on_decorating_result)

拦截工具调用期间 LLM 产生的过渡性消息（如"让我查查..."），避免向用户发送无意义的中间回复。

---

## 核心功能详解

### 主动互动意愿引擎

核心机制是一个指数衰减积分器（Leaky Integrator），模拟人类"越来越想说话"的冲动：

```
S(t) = S(t-1) * exp(-lambda * delta_t / 60) + w
```

其中 `lambda` 为衰减系数（默认 0.9），`w` 为当前消息权重（关键词命中为 2.0，日常闲聊为 1.0）。当积分值超过触发阈值（默认 4.0）时，引擎调用 LLM 进行"是否值得回应"的二次决策。

**三级漏斗机制**用于判定用户活跃状态：

| 级别 | 触发条件 | 效果 |
|------|----------|------|
| L1 | @机器人、命令前缀、引用回复 Bot 消息 | 标记为活跃，触发互动意愿评估 |
| L2 | 唤醒词命中、强 AI 意图句式（"帮我"、"翻译"等） | 标记为活跃，触发互动意愿评估 |
| L3 | 用户在 30 秒内有过 L1/L2 触发 | 加载画像信息 |

**有趣/无聊动态阈值**（5.1.0 新增）：LLM 在评估互动意愿时会判断当前对话"有趣"还是"无聊":

- 有趣判定：降低触发阈值至 `eavesdrop_threshold_min`，增加积分器欲望值
- 无聊判定：提高触发阈值至 `eavesdrop_threshold_max`，降低 SAN 精力值

**信息熵检测**：基于 zlib 压缩比检测消息的信息量。当群聊持续出现低信息量内容时，AI 进入"无聊状态"，拒绝回复或以傲慢语气应对。

**内心独白**：LLM 在判定为 IGNORE（不值得回应）时仍会输出一段简短的内心独白并缓存。下次真正发言时将独白注入回复内容，营造"憋了半天才开口"的自然感。

### 滑动上下文窗口

按 Token 预算（默认 4000）为每个群维护消息滑动窗口。Token 超限时自动淘汰最旧消息。窗口内容在每次 LLM 请求时注入 system prompt。

会话缓冲超时后（默认 10 分钟无新消息）自动清理。清理前可选择将内容批量存入知识库，避免碎片化。

### 用户画像系统

画像以 Markdown 文本存储在 `data/profiles/user_{id}.md`。主要特性：

- **分层失活 (Stratified Dropout)**：核心信息（身份、职业等关键词匹配的行）永远保留，边缘信息以 15% 概率随机丢弃，让 AI 的记忆更接近真人
- **记忆模糊化**：画像生成时标注置信度，低置信度的记忆在回复时使用"我隐约记得"、"似乎"等不确定语气
- **情绪依存记忆**：好感度高的用户，AI 偏向回忆愉快经历；好感度低的用户，偏向回忆负面事件
- **惊奇驱动学习**：检测到用户表达认知颠覆（"原来如此"、"没想到"等）时，触发即时画像更新，弥补批量模式的时效空窗

### 做梦机制

每日凌晨（默认 3:00）触发的批量处理任务，包含以下步骤：

1. **用户画像更新** -- 读取活跃用户的历史消息，调用 LLM 生成增量画像。对于已有 50 字以上画像的用户使用增量更新模板，否则使用全量生成
2. **群记忆总结** -- 对每个群的公共知识进行总结整理并存入知识库
3. **跨群知识关联** -- 分析多个群的知识总结，寻找跨领域关联，生成可在后续对话中引用的洞察
4. **好感度恢复** -- 所有低于 50 分的用户好感度小幅回升（"大赦天下"机制，每次恢复 2 分）

做梦任务使用信号量控制并发（默认 3），避免瞬间发起过多 LLM 请求。优先处理漏斗机制标记的活跃用户，剩余名额分配给已有画像文件。

### 情感矩阵

每个用户有一个 0-100 的好感度评分（初始 50）。好感度由 LLM 通过 `update_affinity` 工具自主调整，每次变动上限为 20 分。

| 区间 | 状态 | 行为 |
|------|------|------|
| 80-100 | 信任 | 优待，回忆愉快经历 |
| 60-79 | 友好 | 正常交流 |
| 40-59 | 中立 | 标准响应 |
| 1-39 | 敌对 | 注意负面行为 |
| 0 | 熔断 | 物理拦截所有请求，零 Token 消耗 |

### SAN 精力值

模拟 AI 的心智疲劳。每处理一条消息消耗精力值（默认 2.0），每小时恢复一定量（默认 10）。精力值存储在内存中，插件重启后重置为满值。

| 精力比例 | 状态 | 表现 |
|----------|------|------|
| > 50% | 精力充沛 | 正常回复 |
| 20-50% | 略有疲态 | 回复中可能表现疲惫 |
| < 20% | 疲惫不堪 | 明确表现出不耐烦 |
| 0 | 耗尽 | 拒绝服务 |

### 群体情绪共染

通过正/负面关键词匹配维护每个群的氛围分数（-10 到 +10），氛围状态注入 LLM 上下文。

| 分值 | 氛围 |
|------|------|
| < -5 | 紧张 |
| -5 到 0 | 低沉 |
| 0 | 平静 |
| 0 到 5 | 轻松 |
| > 5 | 热烈 |

### 元编程与多智能体对抗

元编程功能默认关闭。开启后 AI 可以读取自身源码并提出修改提案。提案的处理流程：

1. **AST 安全校验** -- 拦截 `subprocess`、`socket`、`eval`、`exec` 等危险导入和函数调用
2. **多智能体对抗辩论** -- 可配置多个审查 Agent（默认包含"螺丝咕姆"和"阮梅"），进行多轮代码审查
3. **沙盒隔离** -- 代码提案写入独立目录 `code_proposals/`，不直接修改源码
4. **人工审核** -- 最终由管理员审查后手动应用

元编程工具仅管理员可触发（通过框架级 `PermissionType.ADMIN` 装饰器限制）。

> 注意：AST 安全校验不能防御所有绕过手段。开启元编程后请务必保持管理员审核模式。

---

## 指令列表

### 用户指令

| 指令 | 说明 |
|------|------|
| `/reflect` | 手动触发一次自我反省，在下一次对话时执行深度实体提取 |
| `/affinity` | 查看 AI 对你的当前好感度评分和分类状态 |
| `/view_profile [用户ID]` | 查看指定用户的画像信息（不填则查看自己） |
| `/graph_info [用户ID]` | 查看指定用户的关系图谱信息 |
| `/graph_stats [群ID]` | 查看群聊的关系图谱统计 |

### 管理员指令

| 指令 | 说明 |
|------|------|
| `/set_affinity <用户ID> <分数>` | 强制重置指定用户的好感度（0-100） |
| `/delete_profile <用户ID>` | 删除指定用户的画像 |
| `/profile_stats` | 查看画像系统统计信息 |
| `/review_evolutions [页码]` | 列出待审核的人格进化请求（每页 10 条） |
| `/approve_evolution <ID>` | 批准指定的进化请求 |
| `/reject_evolution <ID>` | 拒绝指定的进化请求 |
| `/clear_evolutions` | 清空所有待审核的进化请求 |

---

## LLM 工具

以下工具通过 Function Calling 注册，AI 在对话过程中自主判断是否调用。

### 记忆管理

| 工具 | 说明 |
|------|------|
| `commit_to_memory` | 将重要事实存入长期记忆库 |
| `recall_memories` | 按关键词检索长期记忆 |
| `learn_from_context` | 从当前对话中提取关键信息存入记忆 |
| `upsert_cognitive_memory` | 统一认知记忆接口，按 category 自动分发到对应存储系统 |
| `save_group_knowledge` | 保存群公共知识（群规、约定活动、群共识） |
| `list_memories` | 列出记忆库条目 |
| `delete_memory` | 删除单条记忆 |
| `clear_all_memory` | 清空全部记忆（需管理员权限和 confirm=true） |

### 画像与社交

| 工具 | 说明 |
|------|------|
| `get_user_profile` | 获取当前用户的画像信息 |
| `update_user_profile` | 更新指定用户的画像 |
| `get_user_messages` | 获取用户在当前群的历史消息（最多 1000 条） |
| `update_affinity` | 根据用户言行调整好感度（单次上限 20 分） |

### 上下文

| 工具 | 说明 |
|------|------|
| `get_session_context` | 获取指定群的滑动窗口缓存内容 |

### 系统管理

| 工具 | 说明 |
|------|------|
| `evolve_persona` | 修改系统提示词（人格进化） |
| `list_tools` | 列出当前所有已注册工具及激活状态 |
| `toggle_tool` | 动态激活或停用某个工具 |

### 元编程（需管理员权限）

| 工具 | 说明 |
|------|------|
| `get_plugin_source` | 读取插件指定模块的源码 |
| `update_plugin_source` | 提交代码修改提案 |

---

## 配置参考

所有配置项可通过 AstrBot 后台面板修改，即时生效无需重启。完整定义见 `_conf_schema.json`。

### 基础设定

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `persona_name` | string | 黑塔 | 机器人名称 |
| `persona_title` | string | 人偶负责人 | 机器人头衔/身份 |
| `persona_style` | string | 理性、犀利且专业 | 互动意愿时的语气风格描述 |

### 互动意愿引擎

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `interjection_desire` | int | 5 | 发言意愿（1 极冷淡，10 高度活跃） |
| `critical_keywords` | string | 黑塔\|空间站\|... | 强制触发互动意愿的正则关键词 |
| `leaky_integrator_enabled` | bool | true | 启用泄漏积分器 |
| `leaky_decay_factor` | float | 0.9 | 衰减系数（0-1，越小衰减越快） |
| `leaky_trigger_threshold` | float | 4.0 | 积分器触发阈值 |
| `interest_boost` | float | 2.0 | 关键词命中时的权重增益 |
| `daily_chat_boost` | float | 1 | 日常消息的权重增益 |
| `inner_monologue_enabled` | bool | true | 启用内心独白缓存 |
| `boredom_enabled` | bool | true | 启用信息熵无聊检测 |
| `boredom_threshold` | float | 0.3 | 信息熵阈值（低于此值视为无聊内容） |
| `boredom_consecutive_count` | int | 5 | 连续低信息量消息达到此数量后触发无聊状态 |
| `boredom_sarcastic_reply` | bool | true | 无聊时被@是否输出傲慢回复 |

### 滑动窗口与会话

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `session_max_tokens` | int | 4000 | 每群滑动窗口最大 Token 数 |
| `session_whitelist` | string | "" | 白名单群号（逗号分隔，空表示所有群） |
| `eavesdrop_interval_minutes` | int | 10 | 定时互动意愿检查间隔（分钟） |
| `eavesdrop_message_threshold` | int | 20 | 定时互动意愿触发的消息数阈值（基础值） |
| `eavesdrop_threshold_min` | int | 10 | 有趣判定时的最低阈值 |
| `eavesdrop_threshold_max` | int | 50 | 无聊判定时的最高阈值 |
| `session_cleanup_timeout` | int | 600 | 会话缓冲超时时间（秒） |
| `session_auto_commit` | bool | true | 超时清理时自动存入知识库 |
| `session_commit_threshold` | int | 5 | 存入知识库的最少消息条数 |

### 记忆系统

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `memory_kb_name` | string | self_evolution_memory | 记忆知识库名称 |
| `max_memory_entries` | int | 100 | 知识库最大条目数（超出自动清理最旧条目） |
| `timeout_memory_commit` | float | 10.0 | 写入知识库超时时间（秒） |
| `timeout_memory_recall` | float | 12.0 | 检索知识库超时时间（秒） |
| `enable_context_recall` | bool | true | 启用上下文追踪（用户引用 AI 发言时自动注入） |
| `auto_memory_recall_enabled` | bool | true | 用户提问时自动检索相关记忆注入上下文 |

### 画像与做梦

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_profile_update` | bool | true | 启用用户画像更新 |
| `profile_group_whitelist` | string | "" | 画像构建群白名单（逗号分隔，空表示所有群） |
| `profile_precision_mode` | string | simple | 画像精度模式（simple: Markdown 摘要） |
| `dropout_enabled` | bool | true | 启用分层失活 |
| `dropout_edge_rate` | float | 0.15 | 边缘信息随机丢弃概率 |
| `dream_enabled` | bool | true | 启用凌晨做梦机制 |
| `dream_schedule` | string | 0 3 * * * | 做梦计划（Cron 表达式） |
| `dream_max_users` | int | 20 | 做梦最大处理用户数 |
| `dream_concurrency` | int | 3 | 做梦并发数 |
| `surprise_enabled` | bool | true | 启用惊奇驱动学习 |
| `surprise_boost_keywords` | string | 我错了\|原来如此\|... | 惊奇关键词（\| 分隔） |

### 精力值与情绪

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `san_enabled` | bool | true | 启用 SAN 精力值系统 |
| `san_max` | int | 100 | 最大精力值 |
| `san_cost_per_message` | float | 2.0 | 每条消息消耗精力 |
| `san_recovery_per_hour` | int | 10 | 每小时恢复精力 |
| `san_low_threshold` | int | 20 | 低精力阈值（低于此值表现疲态） |
| `group_vibe_enabled` | bool | true | 启用群体情绪共染 |

### 元编程与对抗

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `allow_meta_programming` | bool | false | 启用元编程（危险功能） |
| `debate_enabled` | bool | true | 启用多智能体对抗审查 |
| `debate_rounds` | int | 3 | 对抗辩论轮数 |
| `debate_system_prompt` | string | 你是一个无情的安全审查员... | 审查 Agent 的系统提示词 |
| `debate_criteria` | string | 安全漏洞\|逻辑错误\|... | 审查标准（\| 分隔） |
| `debate_agents` | string | JSON 数组 | 审查智能体列表 |
| `graph_enabled` | bool | true | 启用关系图谱 RAG |

---

## 提示词自定义

所有提示词集中在 `prompts.yaml` 中管理。修改后重启或热重载插件即可生效。

| 路径 | 用途 |
|------|------|
| `persona.anchor` | AI 核心人设锚点 |
| `persona.communication` | 日常交流准则与工具调用指引 |
| `persona.meltdown` | 好感度熔断时的固定回复 |
| `eavesdrop.system` | 互动意愿决策的系统提示 |
| `eavesdrop.decision` | 互动意愿决策详细逻辑模板 |
| `eavesdrop.inner_monologue` | 内心独白生成指令 |
| `memory.user_summary` | 做梦时用户画像全量总结模板 |
| `memory.user_incremental` | 做梦时用户画像增量更新模板 |
| `memory.user_system` | 画像总结任务的系统提示 |
| `memory.group_summary` | 群记忆总结模板 |
| `memory.group_system` | 群记忆总结的系统提示 |
| `meta.reviewer_*` | 代码审查 Agent 的人设 |
| `boredom.responses` | 无聊时的随机回复列表 |

---

## 数据存储

| 数据类型 | 存储位置 | 格式 |
|----------|----------|------|
| 好感度、进化请求、互动关系 | `data/self_evolution/self_evolution.db` | SQLite (WAL 模式) |
| 用户画像 | `data/self_evolution/profiles/user_{id}.md` | Markdown 文本 |
| 长期记忆 | AstrBot 知识库 (`self_evolution_memory`) | 向量检索 |
| 代码修改提案 | `data/self_evolution/code_proposals/` | .proposal 文件 |
| 滑动窗口、SAN 值、群氛围 | 内存 | 重启后重置 |

### 数据库表结构

| 表名 | 用途 |
|------|------|
| `pending_evolutions` | 人格进化审核队列 |
| `pending_reflections` | 反思标记 |
| `user_relationships` | 用户好感度评分 |
| `user_interactions` | 用户互动关系图谱 |

---

## 定时任务

插件加载后自动注册以下定时任务（通过 AstrBot Cron Manager）：

| 任务名 | 默认时间 | 说明 |
|--------|----------|------|
| SelfEvolution_DailyReflection | 0 3 * * * (每天凌晨 3 点) | 做梦 + 好感度恢复 |
| SelfEvolution_ProfileCleanup | 0 4 * * * (每天凌晨 4 点) | 清理 90 天未更新的画像 |
| SelfEvolution_EavesdropCheck | 每 10 分钟 | 定时互动意愿检查 |

---

## 已知限制

- SAN 精力值仅存储在内存中，插件重启后重置为满值
- 关系图谱的 `get_group_stats` 和 `get_group_members` 尚未完整实现，目前返回空数据
- 元编程的 AST 安全校验无法防御所有攻击手段（如 `importlib`、`getattr` 反射等）
- 信息熵检测基于 zlib 压缩比，对中文内容的检测精度有限
- 每条消息都会查询数据库获取好感度，高频场景下可能存在性能瓶颈

---

## 开源协议

CC BY-NC 4.0 -- 署名-非商业性使用
