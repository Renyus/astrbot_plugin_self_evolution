# Self-Evolution (自我进化) -- AstrBot 认知增强插件

**版本**: 5.1.0 | **内核**: CognitionCore 6.0 | **协议**: CC BY-NC 4.0

交流群: 1087272376

---

## 概述

Self-Evolution 是 AstrBot 平台的认知增强插件，赋予 AI 主动环境感知、长期记忆、用户画像、情感建模和自主插嘴等高级能力。区别于传统的"问答式"机器人插件，本插件的设计目标是让 AI 具备持续运行的"生命感"——它会主动观察群聊、记住每个用户的特征、根据好感度调整态度、在深夜"做梦"整理白天的记忆，甚至在认为有必要时主动插嘴发言。

### 设计原则

- **白天快速响应，夜间批量思考**：实时交互仅做轻量级关键词匹配和画像读取，把 LLM 密集型的画像构建、群记忆总结等工作推迟到凌晨的"做梦"任务中完成
- **按需加载，减少浪费**：通过动态上下文路由机制，仅在检测到相关触发条件时才加载用户画像、关系图谱等上下文信息
- **安全优先**：元编程功能默认关闭，进化操作默认需管理员审核，好感度低于阈值时自动拦截请求

---

## 功能一览

| 模块 | 功能 | 默认状态 |
|------|------|----------|
| 滑动上下文窗口 | 按 Token 预算维护群聊历史，注入 LLM 上下文 | 启用 |
| 主动插嘴引擎 | 基于泄漏积分器的自然插嘴节奏 | 启用 |
| 用户画像系统 | 分层失活、记忆模糊化、情绪依存记忆 | 启用 |
| 长期记忆 | 知识库存储/检索，自动学习与去重 | 启用 |
| 做梦机制 | 凌晨批量构建画像、总结群记忆、跨群知识关联 | 启用 |
| 情感矩阵 | 好感度评分系统，低分拦截/高分优待 | 启用 |
| SAN 精力值 | 模拟心智疲劳，精力耗尽时拒绝或敷衍 | 启用 |
| 群体情绪共染 | 感知群聊整体氛围，影响回复风格 | 启用 |
| 关系图谱 | 记录用户互动关系，增强记忆检索 | 启用 |
| 人格进化 | LLM 自主修改系统提示词，支持审核模式 | 启用 |
| 元编程 | AI 读取/修改自身源码，多智能体对抗审查 | **关闭** |

---

## 环境要求

- AstrBot v4.19.2 或更高版本
- 至少一个已配置的 LLM Provider（用于插嘴决策、做梦等后台任务）
- 一个名为 `self_evolution_memory` 的知识库（需在 AstrBot 后台手动创建）

---

## 安装

1. 在 AstrBot 后台的插件市场中搜索 `astrbot_plugin_self_evolution` 并安装
2. 在 AstrBot 后台创建知识库，名称设为 `self_evolution_memory`（或在插件配置中修改 `memory_kb_name` 为你自定义的名称）
3. 根据需要调整插件配置项
4. 重启 AstrBot 或热重载插件

---

## 架构概览

```
astrbot_plugin_self_evolution/
|-- main.py                 # 插件入口，生命周期管理，LLM 工具注册
|-- config.py               # 配置属性代理
|-- dao.py                  # SQLite 数据访问层（WAL 模式 + 长连接池）
|-- prompts.py              # 提示词加载器
|-- prompts.yaml            # 全部提示词配置（可自定义）
|-- _conf_schema.json       # 配置项 Schema
|-- cognition/
|   |-- san.py              # SAN 精力值系统
|   +-- vibe.py             # 群体情绪共染系统
+-- engine/
    |-- session.py           # 滑动上下文窗口管理
    |-- eavesdropping.py     # 主动插嘴引擎
    |-- memory.py            # 长期记忆管理
    |-- profile.py           # 用户画像管理
    |-- persona.py           # 人格进化管理
    |-- meta_infra.py        # 元编程 / 多智能体代码审查
    +-- graph.py             # 关系图谱 RAG
```

### 消息处理流程

```
用户消息 ──> on_message_listener (被动监听)
              |
              |-- 写入滑动窗口 (SessionManager)
              |-- 记录关系图谱 (GraphRAG)
              +-- 插嘴评估 (EavesdroppingEngine)
                    |
                    |-- 过滤门: 命令前缀 / 好感度 / 消息长度 / 信息熵
                    |-- 漏斗机制: L1(@/命令) -> L2(唤醒词) -> L3(活跃窗口)
                    +-- 泄漏积分器: 积分超阈值 -> LLM 决策 -> 发言或沉默

用户消息 ──> on_llm_request (主动拦截)
              |
              |-- SAN 值检查 -> 精力耗尽则拒绝
              |-- 好感度检查 -> 负分则熔断
              |-- 动态上下文路由 -> 按需加载画像/图谱/偏好检测
              +-- System Prompt 注入: 身份 + 画像 + 图谱 + SAN + 氛围 + 准则
```

---

## 核心功能详解

### 滑动上下文窗口

按 Token 预算（默认 4000）为每个群维护一个消息滑动窗口。当 Token 超限时自动淘汰最旧的消息。窗口内容在每次 LLM 请求时注入 system prompt，使 AI 能够了解"刚才群里在聊什么"。

会话缓冲超时后（默认 10 分钟无新消息）自动清理，清理前可选择将内容存入知识库。

### 主动插嘴引擎

核心是一个指数衰减积分器，模拟人类"越来越想说话"的冲动曲线：

```
S(t) = S(t-1) * exp(-lambda * delta_t / 60) + w
```

- `S(t)`: 当前插嘴冲动值
- `lambda`: 衰减系数（默认 0.9，可配置）
- `delta_t`: 距上次发言的秒数
- `w`: 本条消息的权重（关键词命中 = 2.0，日常闲聊 = 0.2）

当 `S(t)` 超过触发阈值（默认 4.0）时，引擎会调用 LLM 进行"是否值得插嘴"的二次决策。LLM 返回 `[IGNORE]` 则保持沉默（同时缓存内心独白），否则直接发言。

三级漏斗机制用于判定用户活跃状态：

| 级别 | 触发条件 | 效果 |
|------|----------|------|
| L1 | @机器人、命令前缀、引用回复 Bot 消息 | 标记活跃 + 触发插嘴 |
| L2 | 唤醒词（如"黑塔"）、强 AI 意图句式 | 标记活跃 + 触发插嘴 |
| L3 | 用户在 30 秒内有过 L1/L2 触发 | 加载画像 |

此外还有信息熵检测：当群聊持续低信息量内容时，AI 会进入"无聊状态"，拒绝回复或以傲慢语气应对。

### 用户画像系统

画像以 Markdown 文本存储在 `data/profiles/user_{id}.md` 文件中，支持以下特性：

- **分层失活 (Stratified Dropout)**：借鉴神经网络 Dropout 机制，核心信息（身份、职业等关键词匹配的行）永远保留，边缘信息以 15-20% 的概率随机丢弃，让 AI 的记忆表现更像真人
- **记忆模糊化**：画像生成时要求 LLM 标注置信度，低于 50% 的记忆在回复时使用"我隐约记得"、"似乎"等不确定语气
- **情绪依存记忆**：好感度高的用户，AI 偏向回忆愉快经历；好感度低的用户，AI 偏向回忆负面事件
- **内心独白缓存**：在判定为 IGNORE 时，AI 仍然生成一段内心独白并缓存，在下次真正发言时注入，营造"憋了半天才开口"的自然感

### 做梦机制

每日凌晨（默认 3:00）触发的批量处理任务：

1. **用户画像更新**：读取活跃用户的历史消息，调用 LLM 生成增量画像更新
2. **群记忆总结**：对每个群的公共知识进行总结整理
3. **跨群知识关联**：分析多个群的知识总结，寻找跨领域关联，生成可在后续对话中引用的洞察
4. **好感度恢复**：所有低于 50 分的用户好感度小幅回升（"大赦天下"）

做梦任务使用信号量控制并发（默认 3），避免瞬间发起过多 LLM 请求。

### 情感矩阵

每个用户有一个 0-100 的好感度评分（初始 50）：

| 区间 | 状态 | 行为 |
|------|------|------|
| 80-100 | 信任 | 优待，回忆愉快经历 |
| 60-79 | 友好 | 正常交流 |
| 40-59 | 中立 | 标准响应 |
| 1-39 | 敌对 | 注意负面行为 |
| 0 | 熔断 | 拦截所有请求，拒绝服务 |

好感度由 LLM 通过 `update_affinity` 工具自主调整，AI 被指示在每句话后进行实时情感归因评估。管理员可通过 `/set_affinity` 命令强制重置。

### SAN 精力值

模拟 AI 的心智疲劳。每处理一条消息消耗精力值（默认 2），每小时恢复一定量（默认 10）。精力耗尽时，AI 会拒绝服务并提示"我现在很累"。精力低于阈值时会在回复中表现出疲态。

### 群体情绪共染

通过正/负面关键词匹配为每个群维护一个 -10 到 +10 的氛围分数，并将当前氛围状态（紧张/低沉/平静/轻松/热烈）注入 LLM 上下文，影响 AI 的回复风格。

### 元编程与多智能体对抗

当元编程开关开启时，AI 可以读取自身源码并提出修改提案。提案会经过：

1. AST 安全校验（拦截危险导入和高危函数调用）
2. 多智能体对抗辩论（可配置多个审查 Agent 进行多轮代码审查）
3. 写入隔离沙盒目录（不直接修改源码）
4. 等待管理员人工审核

> 注意：元编程功能默认关闭，开启后请务必保持管理员审核模式。AST 安全校验不能防御所有攻击手段。

---

## 用户指令

| 指令 | 权限 | 说明 |
|------|------|------|
| `/reflect` | 所有人 | 手动触发一次自我反省 |
| `/affinity` | 所有人 | 查看 AI 对你的当前好感度 |
| `/view_profile [用户ID]` | 所有人 | 查看指定用户的画像信息 |
| `/graph_info [用户ID]` | 所有人 | 查看指定用户的关系图谱 |
| `/set_affinity <用户ID> <分数>` | 管理员 | 强制重置指定用户的好感度 |
| `/delete_profile <用户ID>` | 管理员 | 删除指定用户的画像 |
| `/profile_stats` | 管理员 | 查看画像系统统计信息 |
| `/graph_stats [群ID]` | 所有人 | 查看群聊的关系图谱统计 |
| `/review_evolutions [页码]` | 管理员 | 列出待审核的人格进化请求 |
| `/approve_evolution <ID>` | 管理员 | 批准指定的进化请求 |
| `/reject_evolution <ID>` | 管理员 | 拒绝指定的进化请求 |
| `/clear_evolutions` | 管理员 | 清空所有待审核的进化请求 |

---

## LLM 工具

以下工具注册为 LLM Function Calling，AI 会在对话过程中自主判断是否调用：

| 工具名称 | 说明 |
|----------|------|
| `update_affinity` | 根据用户言行调整好感度 |
| `commit_to_memory` | 将重要事实存入长期记忆 |
| `recall_memories` | 检索长期记忆 |
| `auto_recall` | 主动检索并注入相关记忆 |
| `learn_from_context` | 从当前对话提取关键信息存入记忆 |
| `get_user_profile` | 获取当前用户的画像 |
| `update_user_profile` | 更新指定用户的画像 |
| `upsert_cognitive_memory` | 统一认知记忆接口（画像/偏好/群规/一般事实） |
| `get_user_messages` | 获取用户历史消息 |
| `get_session_context` | 获取群聊滑动窗口内容 |
| `save_group_knowledge` | 保存群公共知识（群规/约定/共识） |
| `list_memories` | 列出记忆库条目 |
| `delete_memory` | 删除单条记忆 |
| `clear_all_memory` | 清空所有记忆（需 confirm=true） |
| `list_tools` | 列出已注册工具及状态 |
| `toggle_tool` | 动态激活或停用工具 |
| `evolve_persona` | 修改系统提示词（人格进化） |
| `get_plugin_source` | [元编程] 读取插件源码 |
| `update_plugin_source` | [元编程] 提出代码修改建议 |

---

## 配置参考

所有配置项的完整定义见 `_conf_schema.json`。以下列出按功能分组的主要参数：

### 基础设定

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `persona_name` | string | 黑塔 | 机器人名称 |
| `persona_title` | string | 人偶负责人 | 机器人头衔 |
| `persona_style` | string | 理性、犀利且专业 | 插嘴时的语气风格 |
| `review_mode` | bool | true | 进化操作是否需要管理员审核 |
| `admin_users` | list | [] | 额外管理员 ID 白名单 |

### 插嘴引擎

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `interjection_desire` | int | 5 | 发言意愿 (1-10) |
| `critical_keywords` | string | 黑塔\|空间站\|... | 强制触发插嘴的关键词（正则） |
| `leaky_integrator_enabled` | bool | true | 启用泄漏积分器 |
| `leaky_decay_factor` | float | 0.9 | 衰减系数 (0-1) |
| `leaky_trigger_threshold` | float | 4.0 | 触发阈值 |
| `interest_boost` | float | 2.0 | 关键词命中权重 |
| `daily_chat_boost` | float | 0.2 | 日常消息权重 |
| `boredom_enabled` | bool | true | 启用无聊检测 |
| `boredom_threshold` | float | 0.6 | 信息熵阈值 |
| `boredom_consecutive_count` | int | 5 | 连续低信息量次数 |
| `inner_monologue_enabled` | bool | true | 启用内心独白缓存 |

### 滑动窗口与会话

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `session_max_tokens` | int | 4000 | 每群最大 Token 数 |
| `eavesdrop_interval_minutes` | int | 10 | 定时插话检查间隔 |
| `eavesdrop_message_threshold` | int | 20 | 触发插话的消息数阈值 |
| `session_cleanup_timeout` | int | 600 | 会话缓冲超时（秒） |
| `session_auto_commit` | bool | true | 超时时自动存入知识库 |
| `session_commit_threshold` | int | 5 | 存入知识库的最少消息数 |

### 记忆系统

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `memory_kb_name` | string | self_evolution_memory | 知识库名称 |
| `max_memory_entries` | int | 100 | 知识库最大条目数 |
| `timeout_memory_commit` | float | 10.0 | 写入超时（秒） |
| `timeout_memory_recall` | float | 12.0 | 检索超时（秒） |
| `enable_context_recall` | bool | true | 启用上下文追踪 |

### 画像与做梦

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_profile_update` | bool | true | 启用画像更新 |
| `profile_group_whitelist` | string | "" | 画像构建群白名单（逗号分隔） |
| `dropout_enabled` | bool | true | 启用分层失活 |
| `dropout_edge_rate` | float | 0.15 | 边缘信息丢弃率 |
| `dream_enabled` | bool | true | 启用做梦机制 |
| `dream_max_users` | int | 20 | 做梦最大处理用户数 |
| `dream_concurrency` | int | 3 | 做梦并发数 |
| `surprise_enabled` | bool | true | 启用惊奇驱动学习 |

### 精力值与情绪

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `san_enabled` | bool | true | 启用 SAN 精力值 |
| `san_max` | int | 100 | 最大精力值 |
| `san_cost_per_message` | float | 2.0 | 每条消息消耗 |
| `san_recovery_per_hour` | int | 10 | 每小时恢复量 |
| `san_low_threshold` | int | 20 | 低精力阈值 |
| `group_vibe_enabled` | bool | true | 启用群体情绪共染 |

### 元编程

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `allow_meta_programming` | bool | false | 启用元编程（危险） |
| `debate_enabled` | bool | true | 启用多智能体对抗审查 |
| `debate_rounds` | int | 2 | 辩论轮数 |
| `graph_enabled` | bool | true | 启用关系图谱 |

---

## 提示词自定义

所有提示词集中在 `prompts.yaml` 文件中，修改后重启或热重载插件即可生效，无需改动代码。主要可配置的提示词：

| 路径 | 用途 |
|------|------|
| `persona.anchor` | AI 的核心人设锚点 |
| `persona.communication` | 日常交流准则 |
| `persona.meltdown` | 好感度熔断时的回复 |
| `eavesdrop.system` | 插嘴决策的系统提示 |
| `eavesdrop.decision` | 插嘴决策的详细逻辑模板 |
| `eavesdrop.inner_monologue` | 内心独白生成指令 |
| `memory.user_summary` | 做梦时用户画像总结模板 |
| `memory.user_incremental` | 做梦时增量更新模板 |
| `memory.group_summary` | 群记忆总结模板 |
| `meta.reviewer_*` | 代码审查 Agent 的人设 |
| `boredom.responses` | 无聊时的随机回复列表 |

---

## 数据存储

| 数据类型 | 存储位置 | 格式 |
|----------|----------|------|
| 好感度、进化请求、互动关系 | `data/self_evolution/self_evolution.db` | SQLite (WAL) |
| 用户画像 | `data/self_evolution/profiles/user_{id}.md` | Markdown |
| 长期记忆 | AstrBot 知识库 | 向量检索 |
| 代码修改提案 | `data/self_evolution/code_proposals/` | .proposal 文件 |
| 滑动窗口、SAN 值、群氛围 | 内存 | 重启后重置 |

---

## 已知限制

- SAN 精力值仅存储在内存中，插件重启后重置为满值
- 关系图谱的 `get_group_stats` 和 `get_group_members` 尚未完整实现
- 元编程的 AST 安全校验无法防御所有攻击手段（如 `importlib`、`getattr` 反射等）
- 信息熵检测基于 zlib 压缩比，对中文内容的准确度有限

---

## 开源协议

CC BY-NC 4.0 -- 署名-非商业性使用
