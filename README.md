# 自我进化

AstrBot 插件 `astrbot_plugin_self_evolution`。

这个插件的目标不是单纯给机器人加几条指令，而是给 AstrBot 增加一层"认知增强"能力：让机器人在实时对话之外，还能维护用户画像、会话反思、长期总结记忆、主动互动意愿和情绪状态。

当前版本已经同时支持群聊和私聊场景，并且针对 NapCat 的消息结构做了适配。

## 交流与反馈

- QQ 交流群：`1087272376`
- 群名称：`self_evolution插件交流反馈群`

---

## 模块分层说明

本插件按功能稳定性分为三层维护：

| 层级 | 定位 | 原则 |
|------|------|------|
| **核心模块** | 插件的主体价值 | 默认启用，配置尽量少，测试覆盖最高，不轻易改行为语义 |
| **可选模块** | 增强体验 | 可配置开关，出问题不拖垮核心链路 |
| **实验模块** | 探索性强 | 单独开关，明确标记为实验性，不绑进核心记忆链 |

---

## 核心模块

### 1. Prompt 注入主链

在每次进入 LLM 前，插件会按需向提示词注入以下信息：

- 发送者信息
- 群聊或私聊来源
- 引用和 `@` 关系
- 群消息历史
- 用户画像摘要
- 会话反思结果
- 好感度状态

实现位置：[main.py](./main.py)

### 2. 结构化人物记忆

插件会为用户维护一份本地画像文件，用来记录稳定偏好、身份信息、行为特征和对话印象。

支持：手动创建 / 更新 / 查看 / 删除画像，定时自动构建，私聊画像。

实现位置：[engine/profile.py](./engine/profile.py)、[commands/profile.py](./commands/profile.py)

### 3. 长期会话记忆

插件会在计划时间汇总前一自然日（00:00-24:00）的群聊或私聊消息，生成会话总结，并写入 AstrBot 知识库。

如果某一天的总结被重复执行，插件会覆盖同一天的旧总结，而不是继续往知识库里堆重复文档。私聊 scope 会被持久化记录，重启后不会丢失。

实现位置：[engine/memory.py](./engine/memory.py)、[scheduler/tasks.py](./scheduler/tasks.py)

### 4. 记忆路由

它决定一条信息该去哪层记忆（结构化画像 / 长期知识库 / 反射提示），是整个认知体系的中枢。

实现位置：[engine/memory_router.py](./engine/memory_router.py)

### 5. 会话反思

插件支持在对话后生成一次性"会话反思"，内容包括本轮自我校准建议、可写入画像的明确事实、需要纠正的认知偏差。这些信息会在下次相关对话时注入到提示词中。

每日批处理生成的会话日报也会严格按前一自然日（00:00-24:00）取消息。

实现位置：[engine/reflection.py](./engine/reflection.py)

### 6. 调度层

核心后台任务都靠它编排，包括每日批处理、每日会话总结、自动画像构建、过期画像清理。

实现位置：[scheduler/tasks.py](./scheduler/tasks.py)、[scheduler/register.py](./scheduler/register.py)

### 7. 命令层

用户和管理员能稳定操作核心能力的入口。

实现位置：[commands/profile.py](./commands/profile.py)、[commands/admin.py](./commands/admin.py)、[commands/system.py](./commands/system.py)

---

## 可选模块

以下功能默认开启，可按需关闭。关闭后不影响核心记忆链路。

### 好感度

决定机器人对用户的整体态度，支持每日小幅恢复（"大赦天下"）。

实现位置：[dao.py](./dao.py)

### SAN 精力系统

模拟精力或心智疲劳，影响回复风格和是否继续服务。

实现位置：[cognition/san.py](./cognition/san.py)

### 主动 / 被动互动

- 被动互动：监听所有消息，根据关键词、引用、`@`、信息熵和积分器决定要不要接话
- 主动插嘴：定时检查群消息，在满足阈值时主动参与讨论

实现位置：[engine/eavesdropping.py](./engine/eavesdropping.py)

### 管理员辅助命令

数据库统计、闭嘴控制、表情包管理与统计。

实现位置：[commands/admin.py](./commands/admin.py)、[commands/sticker.py](./commands/sticker.py)

---

## 实验模块

以下功能探索性强、波动大、验证成本高，明确标记为实验性。不绑进核心记忆链，不影响主回复稳定性。

### 表情包与娱乐

表情包学习、打标签、今日老婆等轻量娱乐功能。

实现位置：[engine/entertainment.py](./engine/entertainment.py)、[commands/sticker.py](./commands/sticker.py)

### 元编程与自我进化

人格进化提案与审核、代码修改提案、多轮审查链路。适合管理员在测试环境手动审查。

实现位置：[engine/persona.py](./engine/persona.py)、[engine/meta_infra.py](./engine/meta_infra.py)

---

## 最小可用配置

只需以下配置即可启用核心模块：

1. 在 AstrBot 后台安装插件 `astrbot_plugin_self_evolution`
2. 创建一个基础知识库，名称设置为 `memory_kb_name`（默认 `self_evolution_memory`）
3. 至少一个已配置的对话模型 Provider
4. NapCat 作为消息协议后端
5. 重载插件或重启 AstrBot

---

## 配置参考

### 核心记忆配置

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `memory_kb_name` | `self_evolution_memory` | 基础知识库名称 |
| `memory_fetch_page_size` | `500` | 每次翻历史记录的分页大小 |
| `memory_summary_chunk_size` | `200` | 分段总结时的 LLM chunk 大小 |
| `memory_summary_schedule` | `0 3 * * *` | 每日会话总结时间 |
| `reflection_schedule` | `0 2 * * *` | 每日批处理时间 |
| `profile_msg_count` | `500` | 构建画像时读取的消息数 |
| `enable_profile_injection` | `true` | 是否向提示词注入画像摘要 |
| `enable_profile_fact_writeback` | `true` | 是否将反思事实写回画像 |
| `enable_kb_memory_recall` | `true` | 是否在提示词中召回知识库记忆 |
| `inject_group_history` | `true` | 是否注入群历史 |
| `group_history_count` | `10` | 注入多少条群历史 |
| `auto_profile_enabled` | `true` | 是否开启自动画像构建 |
| `auto_profile_schedule` | `0 0 * * *` | 自动画像构建时间 |
| `auto_profile_batch_size` | `3` | 每批处理群数 |
| `auto_profile_batch_interval` | `30` | 批次间隔分钟数 |

### 行为配置（可选模块）

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `interject_enabled` | `false` | 是否启用主动插嘴 |
| `interject_interval` | `30` | 检查间隔（分钟） |
| `interject_trigger_probability` | `0.5` | 触发概率 |
| `interject_min_msg_count` | `10` | 最少新增消息数 |
| `interject_require_at` | `true` | 是否要求 `@` 机器人 |
| `interject_cooldown` | `30` | 冷却时间（分钟） |
| `san_enabled` | `true` | 是否启用 SAN 精力系统 |
| `san_max` | `100` | 最大精力值 |
| `san_low_threshold` | `20` | 低精力阈值 |
| `san_auto_analyze_enabled` | `true` | 启用自动分析 |
| `san_analyze_interval` | `30` | 分析间隔（分钟） |

### 实验配置

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `sticker_learning_enabled` | `false` | 是否启用表情包学习 |
| `sticker_freq_threshold` | `2` | 同图片发送多少次才判定为表情包 |
| `persona_evolution_enabled` | `false` | 是否启用人格进化提案 |
| `debate_enabled` | `true` | 是否启用代码审查辩论链 |
| `allow_meta_programming` | `false` | 是否允许元编程 |

---

## 整体工作方式

```
当前消息层
  └─ 接收消息、解析引用/@、注入即时上下文
         ↓
Prompt 注入主链（main.py）
  ├─ 画像摘要（engine/profile.py）
  ├─ 知识库召回（engine/memory.py）
  ├─ 会话反思（engine/reflection.py）
  └─ 行为提示（cognition/san.py、engine/eavesdropping.py）
         ↓
长期记忆层（engine/memory.py）
  └─ 写入 AstrBot 知识库，供后续召回
```

## 适用场景

- 想让机器人记住用户长期偏好、说话风格和历史关系
- 想让机器人根据群氛围决定是否参与讨论
- 想把聊天内容沉淀成可跨天召回的长期记忆
- 想把"短期上下文、结构化画像、长期知识库记忆"组合起来使用

## 环境要求

- AstrBot 4.19.2 或更高版本
- 至少一个已配置的对话模型 Provider
- NapCat 作为消息协议后端
- 一个基础知识库，名称与 `memory_kb_name` 配置一致

## 安装

1. 在 AstrBot 后台安装插件 `astrbot_plugin_self_evolution`
2. 在 AstrBot 后台创建一个基础知识库
3. 知识库名称设置为 `memory_kb_name` 对应的值，默认是 `self_evolution_memory`
4. 按需调整插件配置
5. 重载插件或重启 AstrBot

## 知识库说明

### 为什么还要手动创建基础知识库

插件把 `memory_kb_name` 指向的知识库当成"基础入口"，承担两件事：

- 作为长期总结功能的启用锚点
- 作为后续自动创建各个会话隔离知识库时的模板来源

这个基础知识库最好保留，不建议删除。

### 总结现在是怎么存的

新版本中，会话总结不再默认混写到一个库里，而是按会话范围隔离：

- 群聊 `6001` → `self_evolution_memory__scope__g_6001`
- 私聊 `7001` → `self_evolution_memory__scope__p_7001`

这样避免群 A 的总结被群 B 召回，也避免私聊总结串进群聊。

### AstrBot 怎么用到这些总结

当前会话启用知识库召回后，插件会自动把当前会话绑定到对应的 scope 知识库。主链路召回时优先看到"当前群 / 当前私聊自己的总结"。

## 群聊和私聊支持情况

### 已支持

- 群聊画像 / 私聊画像
- 群聊历史消息读取 / 私聊历史消息读取
- 群聊会话总结 / 私聊会话总结
- 群聊每日批处理 / 私聊每日批处理
- 群聊长期总结召回 / 私聊长期总结召回

### 仍以群聊为主的能力

- 主动插嘴
- 群氛围分析
- 部分表情包学习逻辑

## 命令

### 用户命令

| 命令 | 说明 |
|------|------|
| `/sehelp` | 显示插件帮助 |
| `/version` | 显示版本 |
| `/reflect` | 手动触发会话反思 |
| `/affinity` | 查看当前好感度 |
| `/今日老婆` | 今日老婆 |
| `/view [用户ID]` | 查看画像 |
| `/create [用户ID]` | 创建画像 |
| `/update [用户ID]` | 更新画像 |
| `/shut [分钟]` | 临时闭嘴 |

### 管理员命令

| 命令 | 说明 |
|------|------|
| `/set_affinity <用户ID> <分数>` | 设置好感度 |
| `/delete_profile <用户ID>` | 删除画像 |
| `/profile_stats` | 查看画像统计 |
| `/review_evolutions [页码]` | 查看待审核进化 |
| `/approve_evolution <ID>` | 批准进化 |
| `/reject_evolution <ID>` | 拒绝进化 |
| `/clear_evolutions` | 清空进化队列 |
| `/sticker <操作>` | 表情包管理 |
| `/db <操作>` | 数据库操作 |

## LLM 工具

| 工具 | 说明 |
|------|------|
| `get_user_profile` | 获取当前用户画像 |
| `upsert_cognitive_memory` | 写入用户画像记忆 |
| `get_user_messages` | 获取用户历史消息 |
| `update_affinity` | 调整好感度 |
| `evolve_persona` | 提交人格进化提案 |
| `list_tools` | 查看工具列表 |
| `toggle_tool` | 启停工具 |
| `get_plugin_source` | 读取插件源码 |
| `update_plugin_source` | 提交代码修改提案 |
| `list_stickers` | 列出表情包 |
| `send_sticker` | 发送表情包 |

## 后台任务

插件加载后会注册多类后台任务：

| 任务 | 层级 | 说明 |
|------|------|------|
| `SelfEvolution_DailyReflection` | 核心 | 每日批处理，生成日报并刷新画像、恢复好感度 |
| `SelfEvolution_MemorySummary` | 核心 | 每日会话总结 |
| `SelfEvolution_ProfileBuild` | 核心 | 自动画像构建 |
| `SelfEvolution_ProfileCleanup` | 核心 | 清理过期画像 |
| `SelfEvolution_SANAnalyze` | 可选 | SAN 定时分析 |
| `SelfEvolution_Interject` | 可选 | 主动插嘴检查 |
| `SelfEvolution_StickerTag` | 实验 | 表情包打标签 |

## 数据存储

| 数据类型 | 位置 |
|----------|------|
| 好感度、日报、会话反思、表情包 | `data/plugin_data/self_evolution/self_evolution.db` |
| 用户画像 | `data/plugin_data/self_evolution/profiles/*.yaml` |
| 代码提案 | `data/plugin_data/self_evolution/code_proposals/` |
| 会话总结长期记忆 | AstrBot 知识库 |
| SAN、主动插嘴运行态 | 内存（插件重启后重置）|

## 项目结构

```text
astrbot_plugin_self_evolution/
|-- main.py                      # 核心：Prompt 注入主链
|-- config.py                    # 核心：配置管理
|-- dao.py                      # 核心/可选：数据持久化
|-- _conf_schema.json            # 配置 schema
|-- metadata.yaml
|-- prompts_injection.yaml
|--
|-- cognition/
|   +-- san.py                  # 可选：SAN 精力系统
|--
|-- engine/
|   |-- context_injection.py     # 核心：上下文注入
|   |-- eavesdropping.py        # 可选：主动/被动互动
|   |-- entertainment.py        # 实验：表情包与娱乐
|   |-- memory.py               # 核心：长期会话记忆
|   |-- memory_router.py        # 核心：记忆路由中枢
|   |-- meta_infra.py          # 实验：元编程与代码审查
|   |-- persona.py             # 实验：人格进化
|   |-- profile.py              # 核心：结构化人物记忆
|   +-- reflection.py           # 核心：会话反思
|--
|-- commands/                   # 核心/可选：命令入口
|   |-- admin.py               # 可选：管理员命令
|   |-- common.py              # 核心：命令层公共基础设施
|   |-- profile.py            # 核心：画像命令
|   |-- sticker.py            # 可选：表情包命令
|   +-- system.py             # 核心：系统命令
|--
|-- scheduler/                  # 核心：调度编排
|   |-- register.py
|   +-- tasks.py
```

## 当前已修正的重要行为

- NapCat 历史消息读取已统一按 `sender.user_id` 处理
- 私聊画像、私聊会话总结、私聊批处理已打通
- 用户画像文件命名已稳定化，避免昵称变化导致读到旧档
- 好感度缓存已修正，不会因重置或每日恢复留下旧值
- 主动插嘴的 `@` 门槛已配置化
- 长期总结知识库已按会话隔离，不再默认混库召回
- 清理会话总结时，不再默认一把清整个知识库

## 已知限制

- SAN 和部分主动互动状态只存在内存里，插件重启后会重置
- 主动插嘴仍主要是群聊能力，私聊不建议启用类似策略
- 元编程相关能力更适合管理员手动审查，不建议直接在生产群放开
- 基础知识库 `memory_kb_name` 目前仍需要保留，不能随意删除

## 协议

CC BY-NC 4.0
