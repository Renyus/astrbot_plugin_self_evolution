# 自我进化 -- AstrBot 认知增强插件

**版本**: Ver 2.2.0 | **内核**: CognitionCore 7.0 | **协议**: CC BY-NC 4.0

交流群: 1087272376

---

## 概述

自我进化是一个面向 AstrBot 平台的认知增强插件。它赋予 AI 主动环境感知、长期记忆、用户画像、情感建模和自主互动意愿等能力，让 AI 从被动的问答工具升级为具备持续"生命感"的智能体。

插件的核心设计思想是"定时批量处理 + NapCat API 深度整合"：利用 NapCat API 直接获取群消息进行画像构建、群聊总结、SAN 分析等工作，实时交互保持轻量级响应。

---

## 功能模块

| 模块 | 说明 | 状态 |
|------|------|------|
| SAN 精力值系统 | 定时分析群状态，动态调整 AI 精力值 | 启用 |
| 用户画像 | 基于 NapCat API 获取消息，手动指令创建/更新 | 启用 |
| 每日群聊总结 | 定时获取群消息，LLM 总结后存入知识库 | 启用 |
| 好感度系统 | 用户情感评分与熔断机制 | 启用 |
| 欲望积分器 | 模拟人类情绪波动，触发主动插嘴 | 启用 |
| 主动插嘴 | 定时检查群氛围，AI 自主决定是否插嘴 | 关闭（默认） |
| 人格进化 | AI 自主修改系统提示词，支持管理员审核 | 启用 |
| 元编程 | AI 读取/修改自身源码，多智能体对抗审查 | 关闭 |
| 表情包学习 | 自动学习群友表情包，AI 主动发送活跃气氛 | 关闭 |
| 今日老婆 | 随机抽取群友作为今日老婆 | 启用 |

---

## 环境要求

- AstrBot v4.19.2 或更高版本
- 至少一个已配置的 LLM Provider
- 一个名为 `self_evolution_memory` 的知识库（需在 AstrBot 后台手动创建）
- NapCat / go-cqhttp 作为消息协议后端

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
|-- main.py                      # 入口文件，生命周期管理，LLM 工具注册
|-- config.py                   # 配置属性代理，所有配置项集中定义
|-- dao.py                     # SQLite 数据访问层（好感度、进化审核等）
|-- _conf_schema.json          # AstrBot 配置面板 Schema
|-- metadata.yaml               # 插件元信息
|-- prompts_injection.yaml      # Prompt 注入模板
|-- ruff.toml                  # 代码格式化配置
|-- cognition/
|   |-- __init__.py
|   +-- san.py                 # SAN 精力值系统
|-- engine/
|   |-- __init__.py            # 模块导出
|   |-- eavesdropping.py        # 欲望积分器、主动插嘴
|   |-- memory.py               # 每日群聊总结
|   |-- profile.py              # 用户画像管理
|   |-- persona.py              # 人格进化管理
|   |-- meta_infra.py           # 元编程基础设施
|   |-- entertainment.py         # 娱乐功能（表情包、今日老婆）
|   +-- context_injection.py    # 上下文注入
|-- commands/
|   |-- __init__.py            # 命令模块导出
|   |-- profile.py             # 画像相关命令
|   |-- sticker.py             # 表情包命令
|   |-- admin.py               # 管理命令
|   +-- system.py              # 系统命令
+-- scheduler/
    |-- __init__.py            # 调度模块导出
    |-- tasks.py               # 定时任务回调
    +-- register.py            # 任务注册逻辑
```

---

## 核心功能详解

### 1. SAN 精力值系统

模拟 AI 的心智疲劳，通过定时分析群状态动态调整精力值。

**工作流程**：
1. 定时任务触发（默认每 30 分钟）
2. 通过 NapCat API 获取各群最近消息
3. 调用 LLM 分析群活跃度和情绪倾向
4. 根据分析结果调整 SAN 值

**SAN 调整规则**：

| 群状态 | SAN 变化 |
|--------|---------|
| 高活跃 + 正面情绪 | +5 |
| 中活跃 + 中性情绪 | 0 |
| 低活跃 | -3 |
| 负面情绪/有节奏 | -5 |

**SAN 值状态**：

| 精力比例 | 状态 | 表现 |
|----------|------|------|
| > 50% | 精力充沛 | 正常回复 |
| 20-50% | 略有疲态 | 回复中可能表现疲惫 |
| < 20% | 疲惫不堪 | 明确表现出不耐烦 |
| 0 | 耗尽 | 拒绝服务 |

### 2. 用户画像系统

基于 NapCat API 获取用户在群里的消息记录，手动指令触发画像构建。

**触发方式**：
- `/create` - 手动创建画像（获取最近 500 条消息）
- `/update` - 手动更新画像（增量更新）
- `/view` - 查看画像

**权限规则**：
- 普通用户：只能操作自己的画像
- 管理员：可以指定用户操作

**画像格式**：Markdown 文本，存储在 `data/plugin_data/self_evolution/profiles/user_{id}.md`

### 3. 每日群聊总结

定时获取群消息，LLM 总结后存入知识库。

**触发时间**：每天凌晨 3 点（可配置）

**工作流程**：
1. 获取所有监听群的消息（默认 500 条）
2. 调用 LLM 生成群聊总结
3. 存入知识库

**配置项**：
- `memory_summary_schedule` - Cron 表达式
- `memory_msg_count` - 获取消息数

### 4. 好感度系统

每个用户有一个 0-100 的好感度评分（初始 50）。

**好感度区间**：

| 区间 | 状态 | 行为 |
|------|------|------|
| 80-100 | 信任 | 优待，回忆愉快经历 |
| 60-79 | 友好 | 正常交流 |
| 40-59 | 中立 | 标准响应 |
| 1-39 | 敌对 | 注意负面行为 |
| 0 | 熔断 | 物理拦截所有请求 |

**调整方式**：
- LLM 通过 `update_affinity` 工具自主调整（单次上限 20 分）
- 管理员通过 `/set_affinity` 指令手动设置
- 每天凌晨"大赦天下"：低于 50 分的用户好感度 +2

### 5. 欲望积分器

模拟人类"越来越想说话"的冲动，通过指数衰减积分器实现。

**积分公式**：
```
S(t) = S(t-1) * exp(-λ * Δt / 60) + w
```

- `λ` (leaky_decay_factor): 衰减系数，默认 0.9
- `w` (interest_boost): 消息权重，默认 2.0（关键词命中）

**触发条件**：
- @机器人
- 命令前缀
- 引用回复
- 唤醒词命中
- 强 AI 意图句式

### 6. 主动插嘴（可选）

定时检查群氛围，AI 自主决定是否插嘴。

**默认状态**：关闭

**工作流程**：
1. 定时任务触发（默认每 30 分钟）
2. 通过 NapCat API 获取群消息（默认 100 条）
3. 调用 LLM 判断是否应该插嘴
4. 如果应该插嘴，发送消息

**配置项**：
- `interject_enabled` - 是否启用（默认 false）
- `interject_interval` - 检查间隔（默认 30 分钟）
- `interject_msg_count` - 获取消息数（默认 100）

### 7. 人格进化

AI 自主修改系统提示词，支持管理员审核。

**工作流程**：
1. AI 调用 `evolve_persona` 工具提出修改
2. 生成进化提案，存入待审核队列
3. 管理员通过 `/review_evolutions` 查看
4. 管理员通过 `/approve_evolution` 批准或 `/reject_evolution` 拒绝

### 8. 元编程（危险功能）

默认关闭。开启后 AI 可以读取并修改自身源码。

**安全措施**：
1. AST 安全校验 - 拦截危险导入和函数调用
2. 多智能体对抗辩论 - 可配置多个审查 Agent
3. 沙盒隔离 - 提案写入独立目录
4. 人工审核 - 最终由管理员手动应用

---

## 指令列表

### 用户指令

| 指令 | 说明 |
|------|------|
| `/sehelp` | 显示插件帮助信息 |
| `/version` | 显示插件版本 |
| `/reflect` | 手动触发自我反省 |
| `/affinity` | 查看当前好感度 |
| `/今日老婆` | 随机抽取今日老婆 |
| `/view [用户ID]` | 查看画像（普通用户只能看自己） |
| `/create [用户ID]` | 创建画像（普通用户只能给自己创建） |
| `/update [用户ID]` | 更新画像（普通用户只能更新自己） |
| `/shut` | 让 AI 停止回复 |

### 管理员指令

| 指令 | 说明 |
|------|------|
| `/set_affinity <用户ID> <分数>` | 强制设置好感度（0-100） |
| `/delete_profile <用户ID>` | 删除用户画像 |
| `/profile_stats` | 查看画像统计 |
| `/review_evolutions [页码]` | 查看待审核进化 |
| `/approve_evolution <ID>` | 批准进化 |
| `/reject_evolution <ID>` | 拒绝进化 |
| `/clear_evolutions` | 清空进化队列 |
| `/sticker <操作>` | 表情包管理 |
| `/db <操作>` | 数据库操作 |

---

## LLM 工具

以下工具通过 Function Calling 注册，AI 在对话过程中自主判断是否调用。

### 画像与记忆

| 工具 | 说明 |
|------|------|
| `get_user_profile` | 获取用户画像 |
| `upsert_cognitive_memory` | 存储记忆（仅支持用户画像类别） |
| `get_user_messages` | 获取用户在群里的历史消息 |

### 情感系统

| 工具 | 说明 |
|------|------|
| `update_affinity` | 调整用户好感度 |

### 人格进化

| 工具 | 说明 |
|------|------|
| `evolve_persona` | 修改系统提示词（需审核） |

### 系统管理

| 工具 | 说明 |
|------|------|
| `list_tools` | 列出所有工具 |
| `toggle_tool` | 开关工具 |

### 元编程（需管理员）

| 工具 | 说明 |
|------|------|
| `get_plugin_source` | 读取插件源码 |
| `update_plugin_source` | 提交代码修改提案 |

### 娱乐功能

| 工具 | 说明 |
|------|------|
| `list_stickers` | 列出表情包 |
| `send_sticker` | 发送表情包 |

---

## 配置参考

所有配置项可通过 AstrBot 后台面板修改，即时生效。

### SAN 系统

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `san_enabled` | bool | true | 启用 SAN 系统 |
| `san_max` | int | 100 | 最大精力值 |
| `san_cost_per_message` | float | 2.0 | 每条消息消耗精力 |
| `san_recovery_per_hour` | int | 10 | 每小时恢复精力 |
| `san_low_threshold` | int | 20 | 低精力阈值 |
| `san_auto_analyze_enabled` | bool | true | 启用自动分析 |
| `san_analyze_interval` | int | 30 | 分析间隔（分钟） |
| `san_msg_count_per_group` | int | 50 | 每群获取消息数 |
| `san_high_activity_boost` | int | 5 | 高活跃加成 |
| `san_low_activity_drain` | int | -3 | 低活跃消耗 |
| `san_positive_vibe_bonus` | int | 3 | 正面情绪加成 |
| `san_negative_vibe_penalty` | int | -5 | 负面情绪惩罚 |

### 用户画像

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `profile_msg_count` | int | 500 | 构建画像消息数 |

### 每日总结

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `memory_kb_name` | string | self_evolution_memory | 知识库名称 |
| `memory_msg_count` | int | 500 | 总结消息数 |
| `memory_summary_schedule` | string | 0 3 * * * | 总结计划 |

### 欲望积分器

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `leaky_integrator_enabled` | bool | true | 启用欲望积分器 |
| `leaky_decay_factor` | float | 0.9 | 衰减系数 |
| `leaky_trigger_threshold` | float | 5 | 触发阈值 |
| `interest_boost` | float | 2.0 | 兴趣增益 |
| `desire_cooldown_messages` | int | 5 | 冷却消息数 |
| `desire_cooldown_seconds` | int | 60 | 冷却时长（秒） |
| `eavesdrop_message_threshold` | int | 20 | 偷听消息阈值 |
| `eavesdrop_threshold_min` | int | 10 | 偷听阈值最小值 |
| `eavesdrop_threshold_max` | int | 50 | 偷听阈值最大值 |

### 主动插嘴

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `interject_enabled` | bool | false | 启用主动插嘴 |
| `interject_interval` | int | 30 | 检查间隔（分钟） |
| `interject_msg_count` | int | 100 | 获取消息数 |
| `interject_analyze_count` | int | 15 | 分析消息数 |
| `interject_cooldown` | int | 30 | 冷却时间（分钟） |
| `interject_whitelist` | list | [] | 插嘴白名单群号 |

### 分层失活

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `dropout_enabled` | bool | true | 启用分层失活 |
| `dropout_edge_rate` | float | 0.2 | 边缘信息丢弃概率 |
| `core_info_keywords` | string | 我是谁,我的名字... | 核心信息关键词 |

### 元编程

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `allow_meta_programming` | bool | false | 启用元编程（危险） |
| `review_mode` | bool | true | 管理员审核模式 |
| `debate_enabled` | bool | true | 启用对抗审查 |

### 表情包

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `sticker_learning_enabled` | bool | false | 启用表情包学习 |
| `sticker_target_qq` | string | "" | 学习对象 QQ 号 |
| `sticker_fetch_interval` | int | 5 | 获取间隔（分钟） |
| `sticker_tag_cooldown` | int | 5 | 打标签间隔（分钟） |

### 基础配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `persona_name` | string | 黑塔 | 机器人名称 |
| `admin_users` | list | [] | 管理员列表 |
| `reflection_schedule` | string | 0 2 * * * | 自省计划 |
| `max_prompt_injection_length` | int | 2000 | Prompt 注入最大长度 |

---

## 定时任务

插件加载后自动注册以下定时任务：

| 任务名 | 默认时间 | 说明 |
|--------|----------|------|
| SelfEvolution_DailyReflection | 0 2 * * * | 每日自省标记 |
| SelfEvolution_MemorySummary | 0 3 * * * | 每日群聊总结 |
| SelfEvolution_ProfileCleanup | 0 4 * * * | 清理过期画像 |
| SelfEvolution_SANAnalyze | */30 * * * | SAN 精力分析 |
| SelfEvolution_StickerTag | 每 N 分钟 | 表情包打标签 |
| SelfEvolution_Interject | 每 N 分钟 | 主动插嘴（需启用） |

---

## 数据存储

| 数据类型 | 存储位置 | 格式 |
|----------|----------|------|
| 好感度、进化审核 | `data/plugin_data/self_evolution/*.db` | SQLite |
| 用户画像 | `data/plugin_data/self_evolution/profiles/*.md` | Markdown |
| 长期记忆 | AstrBot 知识库 | 向量检索 |
| 代码提案 | `data/plugin_data/self_evolution/code_proposals/` | 文件 |
| SAN 值、欲望积分 | 内存 | 重启后重置 |

### 数据库表

| 表名 | 用途 |
|------|------|
| `pending_evolutions` | 人格进化审核队列 |
| `user_relationships` | 用户好感度 |
| `stickers` | 表情包存储 |

---

## NapCat API 整合

本插件深度整合 NapCat API 实现以下功能：

| 功能 | API |
|------|-----|
| 获取群消息历史 | `get_group_msg_history` |
| 发送群消息 | `send_group_msg` |
| 获取群信息 | `get_group_info` |
| 获取群成员列表 | `get_group_member_list` |
| 获取成员信息 | `get_group_member_info` |

---

## 与 AstrBot 框架的关系

| 框架功能 | 插件处理方式 |
|----------|-------------|
| LongTermMemory (LTM) | 插件不使用，仅用于漏斗判断 |
| 知识库 (KB) | 插件使用 kb_manager 存储长期记忆 |
| Persona 人格 | 通过 persona_manager 进行人格管理 |
| 图片理解 MCP | 区分已知/未知图片，减少重复调用 |

---

## 已知限制

- SAN 精力值仅存储在内存中，插件重启后重置
- 元编程的 AST 安全校验无法防御所有攻击手段
- 主动插嘴功能需要谨慎使用，避免过度打扰用户

---

## 开源协议

CC BY-NC 4.0 -- 署名-非商业性使用
