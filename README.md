# 自我进化

AstrBot 插件 `astrbot_plugin_self_evolution`。

这个插件的目标不是单纯给机器人加几条指令，而是给 AstrBot 增加一层“认知增强”能力：让机器人在实时对话之外，还能维护用户画像、会话反思、长期总结记忆、主动互动意愿和情绪状态。

当前版本已经同时支持群聊和私聊场景，并且针对 NapCat 的消息结构做了适配。

## 适用场景

- 想让机器人记住用户长期偏好、说话风格和历史关系
- 想让机器人根据群氛围决定是否参与讨论
- 想把聊天内容沉淀成可跨天召回的长期记忆
- 想把“短期上下文、结构化画像、长期知识库记忆”组合起来使用

## 核心能力

### 1. 实时上下文增强

在每次进入 LLM 前，插件会按需向提示词注入以下信息：

- 发送者信息
- 群聊或私聊来源
- 引用和 `@` 关系
- 群消息历史
- 用户画像摘要
- 会话反思结果
- 好感度状态
- SAN 精力状态

主入口在 [main.py](./main.py)。

### 2. 用户画像

插件会为用户维护一份本地画像文件，用来记录稳定偏好、身份信息、行为特征和对话印象。

支持：

- 手动创建画像
- 手动更新画像
- 查看画像
- 删除画像
- 定时自动构建画像
- 私聊画像

实现位置：

- [engine/profile.py](./engine/profile.py)
- [commands/profile.py](./commands/profile.py)

### 3. 会话反思

插件支持在对话后生成一次性“会话反思”，内容包括：

- 本轮对话里机器人的自我校准建议
- 可写入画像的明确事实
- 需要纠正的认知偏差

这些信息会在下次相关对话时注入到提示词中。

实现位置：

- [engine/reflection.py](./engine/reflection.py)

每日批处理生成的会话日报也会严格按前一自然日（00:00-24:00）取消息，而不是简单截取最近若干条历史记录。

### 4. 长期会话总结

插件会在计划时间汇总前一自然日（00:00-24:00）的群聊或私聊消息，生成会话总结，并写入 AstrBot 知识库。

如果某一天的总结被重复执行，插件会覆盖同一天的旧总结，而不是继续往知识库里堆重复文档。

私聊 scope 会被持久化记录，因此即使插件重启，后续的每日总结和每日批处理也不会因为 30 秒活跃窗口失效而丢掉这些私聊会话。

这部分总结用于配合 AstrBot 的知识库召回能力，承担“长期背景记忆”的角色。

实现位置：

- [engine/memory.py](./engine/memory.py)
- [scheduler/tasks.py](./scheduler/tasks.py)

### 5. 被动互动和主动插嘴

插件有两套互动机制：

- 被动互动：监听所有消息，根据关键词、引用、`@`、信息熵和积分器决定要不要接话
- 主动插嘴：定时检查群消息，在满足阈值时主动参与讨论

实现位置：

- [engine/eavesdropping.py](./engine/eavesdropping.py)

### 6. 好感度与 SAN

插件维护两套状态：

- 好感度：决定机器人对用户的整体态度，支持熔断
- SAN：模拟精力或心智疲劳，影响回复风格和是否继续服务

实现位置：

- [dao.py](./dao.py)
- [cognition/san.py](./cognition/san.py)

### 7. 表情包与娱乐功能

插件包含一些轻量娱乐能力：

- 表情包学习
- 表情包打标签
- 表情包发送
- 今日老婆
- 闭嘴功能

实现位置：

- [engine/entertainment.py](./engine/entertainment.py)
- [commands/sticker.py](./commands/sticker.py)

### 8. 人格进化与元编程

插件保留了高级能力：

- 人格进化提案与审核
- 读取插件源码
- 生成代码修改提案
- 多轮审查链路

默认更适合管理员或实验环境使用。

实现位置：

- [engine/persona.py](./engine/persona.py)
- [engine/meta_infra.py](./engine/meta_infra.py)

## 整体工作方式

可以把插件理解成四层：

1. 当前消息层
   负责处理当前用户消息、引用关系、`@`、群历史和即时提示词注入。
2. 结构化认知层
   负责维护用户画像、好感度、会话反思和表情包标签。
3. 后台批处理层
   负责自动画像构建、每日批处理、SAN 分析和主动插嘴检查。
4. 长期记忆层
   负责把会话总结写入 AstrBot 知识库，并交给 AstrBot 主链路召回。

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

这是当前最容易让人困惑的部分，建议认真看一遍。

### 1. 为什么还要手动创建基础知识库

插件现在会把 `memory_kb_name` 指向的知识库当成“基础入口”。

它的作用不是继续把所有总结混存进去，而是承担两件事：

- 作为长期总结功能的启用锚点
- 作为后续自动创建各个会话隔离知识库时的模板来源

所以这个基础知识库现在最好保留，不建议删除。

### 2. 总结现在是怎么存的

新版本中，会话总结不再默认混写到一个库里，而是按会话范围隔离。

例如：

- 群聊 `6001` 会生成类似 `self_evolution_memory__scope__g_6001` 的知识库
- 私聊 `7001` 会生成类似 `self_evolution_memory__scope__p_7001` 的知识库

这样做的目的是避免群 A 的长期总结被群 B 召回，也避免私聊总结串进群聊。

### 3. AstrBot 怎么用到这些总结

如果当前会话已经启用了知识库召回，插件会自动把当前会话绑定到对应的 scope 知识库。

于是 AstrBot 主链路在召回时，优先看到的是“当前群/当前私聊自己的总结”，而不是所有会话混在一起的总结。

### 4. 老数据怎么办

旧版本可能已经把部分总结写进了基础知识库。

当前版本不会再继续往里面混写，但会在查看和清理总结时兼容一部分旧数据。

## 群聊和私聊支持情况

### 已支持

- 群聊画像
- 私聊画像
- 群聊历史消息读取
- 私聊历史消息读取
- 群聊会话总结
- 私聊会话总结
- 群聊每日批处理
- 私聊每日批处理
- 群聊长期总结召回
- 私聊长期总结召回

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

插件注册了以下核心工具：

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

插件加载后会注册多类后台任务，默认包括：

| 任务 | 作用 |
|------|------|
| `SelfEvolution_DailyReflection` | 每日批处理，按前一自然日生成会话日报并刷新画像、恢复好感度 |
| `SelfEvolution_MemorySummary` | 每日会话总结 |
| `SelfEvolution_ProfileBuild` | 自动画像构建 |
| `SelfEvolution_ProfileCleanup` | 清理过期画像 |
| `SelfEvolution_SANAnalyze` | SAN 定时分析 |
| `SelfEvolution_StickerTag` | 表情包打标签 |
| `SelfEvolution_Interject` | 主动插嘴检查 |

## 重要配置

AstrBot 面板里可见的配置很多，下面只列最常用、最影响行为的部分。

### 长期总结和知识库

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `memory_kb_name` | `self_evolution_memory` | 基础知识库名称 |
| `memory_msg_count` | `500` | 每次翻历史记录的分页大小，也是单轮分段总结的消息上限 |
| `memory_summary_schedule` | `0 3 * * *` | 每天执行“前一自然日会话总结”的时间 |

### 用户画像

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `profile_msg_count` | `500` | 构建画像时读取的消息数 |
| `auto_profile_enabled` | `true` | 是否开启自动画像 |
| `auto_profile_schedule` | `0 0 * * *` | 自动画像时间 |
| `auto_profile_batch_size` | `3` | 每批处理群数 |
| `auto_profile_batch_interval` | `30` | 批次间隔分钟数 |

### 主动插嘴

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `interject_enabled` | `false` | 是否启用主动插嘴 |
| `interject_interval` | `30` | 检查间隔分钟数 |
| `interject_min_msg_count` | `10` | 最少新增消息数 |
| `interject_require_at` | `true` | 是否要求最新消息必须 `@` 机器人 |
| `interject_cooldown` | `30` | 冷却时间分钟数 |
| `interject_whitelist` | `[]` | 白名单群列表；空列表表示不过滤 |

### SAN

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `san_enabled` | `true` | 启用 SAN |
| `san_max` | `100` | 最大精力值 |
| `san_low_threshold` | `20` | 低精力阈值 |
| `san_auto_analyze_enabled` | `true` | 启用自动分析 |
| `san_analyze_interval` | `30` | 分析间隔分钟数 |

### 其它常用项

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `reflection_schedule` | `0 2 * * *` | 每天执行“前一自然日会话日报和画像刷新”的时间 |
| `inject_group_history` | `true` | 是否注入群历史 |
| `group_history_count` | `10` | 注入多少条群历史 |
| `debug_log_enabled` | `false` | 是否输出详细调试日志 |
| `sticker_learning_enabled` | `false` | 是否启用表情包学习 |

## 数据存储

| 数据类型 | 位置 |
|----------|------|
| 好感度、日报、会话反思、表情包 | `data/plugin_data/self_evolution/self_evolution.db` |
| 用户画像 | `data/plugin_data/self_evolution/profiles/*.yaml` |
| 代码提案 | `data/plugin_data/self_evolution/code_proposals/` |
| 会话总结长期记忆 | AstrBot 知识库 |
| SAN、主动插嘴运行态 | 内存 |

## 项目结构

```text
astrbot_plugin_self_evolution/
|-- main.py
|-- config.py
|-- dao.py
|-- _conf_schema.json
|-- metadata.yaml
|-- prompts_injection.yaml
|-- cognition/
|   +-- san.py
|-- engine/
|   |-- context_injection.py
|   |-- eavesdropping.py
|   |-- entertainment.py
|   |-- memory.py
|   |-- meta_infra.py
|   |-- persona.py
|   |-- profile.py
|   +-- reflection.py
|-- commands/
|   |-- admin.py
|   |-- profile.py
|   |-- sticker.py
|   +-- system.py
+-- scheduler/
    |-- register.py
    +-- tasks.py
```

## 当前已修正的重要行为

如果你是老用户，下面这些是近几轮修复后最重要的变化：

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
