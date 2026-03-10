# 自我进化 (Self-Evolution) 插件

版本: 4.0.1 (多智能体+关系图谱版)

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)

## 简介

这是一个面向 AstrBot 的 AI 心智模型插件。它赋予大语言模型以下能力：

- 自我迭代反思
- 跨会话长期记忆
- 动态技能管理
- 代码级自我元编程（需手动开启）

## 非商业性使用声明

本项目采用 [CC BY-NC 4.0 (知识共享署名-非商业性使用 4.0 国际许可协议)](LICENSE) 进行授权。任何人均可自由学习、修改和分发本源代码，但严禁将本插件或其衍生版本用于任何商业盈利目的。

---

## 核心功能

### 1. 核心人格进化

当用户对 AI 的回复表现出强烈的情绪（不满或赞赏）时，AI 能结合预设的核心价值观进行辩证自省：

- 若反馈有客观建设性：AI 将主动调用 `evolve_persona` 工具提出修改建议
- 若反馈违背底线：AI 将坚守原则边界并优雅拒绝
- 强制自省：每日凌晨通过 Cron 调度，或由用户指令隐式触发

### 2. 长期记忆库检索

基于知识库组件（如 Milvus / Chroma），解决模型"转身就忘"的问题：

- `commit_to_memory`：存储长期记忆
- `recall_memories`：检索过往记忆
- `auto_recall`：自动将相关记忆注入上下文

### 3. 元级编程

赋予 AI "阅读并修改自身源码"的能力（需在管理面板开启）：

- `get_plugin_source`：读取核心架构源码，支持分模块读取
- `update_plugin_source`：编写代码迭代升级方案
- 安全沙箱：生成的代码不会立刻执行，而是隔离等待人类审查

### 4. 动态技能管理

- `list_tools`：列出可用工具
- `toggle_tool`：自主开关工具

### 5. 情感拦截与主动插嘴

- 情感矩阵：用户出言不逊时会静默扣除积分，积分 <= 0 时实施物理熔断
- 泄漏积分器：替代死板计数器的动态触发机制，引入时间衰减因子
- 环境监听：默默"偷听"群聊并适时介入

### 6. 用户画像系统

- 双轨触发：兴趣关键词命中 或 用户 @ 机器人
- 智能画像提取：从对话片段提取兴趣标签和性格特征
- 分层失活：核心信息永不丢失，边缘信息随机屏蔽增加人味
- 突发偏好检测：弥补 Batch 模式时效性空窗
- 自动注入：有效互动场景下自动将用户画像注入上下文

### 7. 上下文追踪

当用户引用 AI 之前的话时，自动识别上下文，利用 AstrBot 内置消息历史解决"断片"问题。

### 8. 多智能体对抗

- 主控 Agent (黑塔) 生成代码提案
- 审查 Agent (螺丝咕姆) 进行对抗辩论
- 多轮辩论达成共识后才进入人工审核

### 9. 惊奇驱动学习 (Surprise Detection)

- 检测用户认知颠覆/惊喜表达（如"我错了"、"原来如此"、"没想到"等）
- 触发即时画像更新，弥补 Batch 模式时效性空窗
- 新增配置: surprise_enabled, surprise_boost_keywords

### 10. 关系图谱 RAG (GraphRAG)

- 记录用户在群聊中的互动关系
- 追踪用户活跃群组和频繁互动用户
- 关系图谱增强的记忆检索
- 新增配置: graph_enabled

---

## 配置项

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `persona_name` | string | 黑塔 | 机器人的核心名称 |
| `persona_title` | string | 人偶负责人 | 机器人的身份或头衔 |
| `persona_style` | string | 理性、犀利且专业 | 决定插嘴时的语气 |
| `interjection_desire` | int | 5 | 插嘴意愿指数 (1-10)，数值越高越主动 |
| `critical_keywords` | string | (见配置) | 意图预扫描关键词，正则格式 |
| `review_mode` | bool | true | 管理员审核模式，进化申请需审批 |
| `allow_meta_programming` | bool | false | 开启元编程（危险） |
| `memory_kb_name` | string | self_evolution_memory | 知识库名称 |
| `reflection_schedule` | string | 0 3 * * * | 每日自省计划 (Cron) |
| `core_principles` | string | (见默认文本) | 机器人核心锚点 |
| `admin_users` | list | [] | 管理员 ID 列表 |
| `timeout_memory_commit` | float | 10.0 | 存入记忆超时(秒) |
| `timeout_memory_recall` | float | 12.0 | 读取记忆超时(秒) |
| `buffer_threshold` | int | 8 | 触发自省的条数阈值 (旧版) |
| `max_buffer_size` | int | 20 | 缓冲池硬上限 |
| `enable_profile_update` | bool | true | 启用画像更新 |
| `enable_context_recall` | bool | true | 启用上下文追踪 |
| `dream_enabled` | bool | true | 启用做梦机制 |
| `dream_max_users` | int | 20 | 做梦最大处理用户数 |
| `dream_concurrency` | int | 3 | 做梦并发数 |
| `prompt_meltdown_message` | string | (见配置) | 熔断提示词 |
| `prompt_reflection_instruction` | string | (见配置) | 反思指令 |
| `prompt_communication_guidelines` | string | (见配置) | 交流准则 |
| `prompt_eavesdrop_system` | string | (见配置) | 插嘴系统提示词 |
| `dropout_enabled` | bool | true | 启用分层失活 |
| `dropout_edge_rate` | float | 0.15 | 边缘信息失活概率 |
| `leaky_integrator_enabled` | bool | true | 启用泄漏积分器 |
| `leaky_decay_factor` | float | 0.9 | 泄漏衰减系数 |
| `leaky_trigger_threshold` | float | 4.0 | 泄漏积分器触发阈值 |
| `interest_boost` | float | 2.0 | 兴趣话题增益 |
| `daily_chat_boost` | float | 0.2 | 日常话题增益 |
| `debate_enabled` | bool | true | 启用多智能体对抗 |
| `debate_rounds` | int | 2 | 对抗辩论轮数 |
| `debate_system_prompt` | string | (见配置) | 审查 Agent 系统提示词 |
| `debate_criteria` | string | (见配置) | 代码审查标准 |
| `surprise_enabled` | bool | true | 启用惊奇驱动学习 |
| `surprise_boost_keywords` | string | (见配置) | 惊奇关键词 |
| `graph_enabled` | bool | true | 启用关系图谱 RAG |

---

## 管理员指令

- `/reflect` - 强制触发一次自我反思
- `/review_evolutions [页码]` - 查看待审核的进化请求
- `/approve_evolution [ID]` - 批准进化请求
- `/reject_evolution [ID]` - 拒绝进化请求
- `/clear_evolutions` - 清空所有待审核请求
- `/set_affinity [用户ID] [分数]` - 手动修正用户好感度
- `/affinity` - 查看自己的好感度
- `/view_profile [用户ID]` - 查看用户画像
- `/delete_profile [用户ID]` - 删除用户画像（管理员）
- `/profile_stats` - 画像统计（管理员）
- `/graph_info [用户ID]` - 查看用户关系图谱
- `/graph_stats [群号]` - 查看群关系图谱统计

---

## LLM 工具

| 工具名称 | 说明 |
| :--- | :--- |
| `commit_to_memory` | 存入长期记忆 |
| `recall_memories` | 检索记忆 |
| `learn_from_context` | 从对话提取关键信息并存入记忆 |
| `clear_all_memory` | 清空所有记忆 |
| `list_memories` | 列出记忆条目 |
| `delete_memory` | 删除单条记忆 |
| `auto_recall` | 主动注入相关记忆到上下文 |
| `save_group_knowledge` | 保存群规、约定等群公共知识 |
| `get_user_profile` | 获取用户画像 |
| `update_user_profile` | 更新用户画像 |
| `update_affinity` | 调整用户情感积分 |
| `evolve_persona` | 修改系统提示词 |
| `list_tools` | 列出可用工具 |
| `toggle_tool` | 开关工具 |
| `get_plugin_source` | 读取插件源码 |
| `update_plugin_source` | 提出代码修改建议 |
| `get_user_messages` | 获取用户历史消息 |

---

## 目录结构

```
self_evolution/
├── main.py              # 插件入口
├── dao.py               # 数据库访问层
├── engine/
│   ├── eavesdropping.py # 插嘴引擎
│   ├── memory.py        # 记忆管理
│   ├── persona.py       # 人格进化
│   ├── profile.py       # 用户画像
│   ├── meta_infra.py    # 元编程基础设施
│   └── graph.py         # 关系图谱 RAG
├── _conf_schema.json    # 配置 schema
├── metadata.yaml        # 插件元信息
└── README.md            # 本文档
```

---

## 测试

单元测试代码位于 `test` 分支：

```bash
git checkout test
pip install pytest pytest-asyncio
pytest tests/ -v
```

---

## 开源协议

本项目采用 [CC BY-NC 4.0 (署名-非商业性使用 4.0 国际)](LICENSE) 协议授权。

- 您可以：自由地共享、演绎、修改本插件
- 您必须：保留原作者署名
- 不可用于商业目的
- 免责声明：作者不对使用本插件造成的任何损失负责
