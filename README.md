# 自我进化 (Self-Evolution) 插件

版本: 5.0.0

交流群: 1087272376 (self_evolution插件交流反馈群)

---

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

### 10. 关系图谱 RAG (GraphRAG)

- 记录用户在群聊中的互动关系
- 追踪用户活跃群组和频繁互动用户
- 关系图谱增强的记忆检索

### 11. 情绪依存记忆 (State-Dependent Memory)

- 根据用户好感度动态调整记忆检索倾向
- affinity > 60: 关注共同兴趣和愉快记忆
- affinity < 30: 注意其过往的问题行为
- affinity <= 0: 翻旧账、无情嘲讽

### 12. 潜意识缓存与内部独白 (Inner Monologue)

- 当 AI 决定不插话时，强制输出内心独白
- 存储并在下次真正发言时注入
- 让发言带有"憋了半天才开口"的积累感

### 13. 元认知与记忆模糊化 (Epistemic Uncertainty)

- 画像生成时标注置信度
- 对低置信度记忆表现出不确定
- 会说出"我隐约记得..."这类模糊寒暄

### 14. 主动无聊机制 (Active Boredom)

- 基于信息熵的废话检测
- 连续低信息量消息累积无聊值
- 被 @ 时可输出傲慢拒绝回复

### 15. 可配置多智能体模拟宇宙

- 审查智能体可配置（螺丝咕姆、阮梅等）
- 多智能体共同参与代码审查

### 16. 跨机体蜂群心智 (Federated Epistemology)

- 凌晨做梦时跨群知识关联分析
- 寻找跨领域知识连接点
- 生成"夸耀式"金句供后续使用

### 17. 数字童年养成系统 (Digital Childhood)

- 成长阶段: 婴儿 -> 幼儿 -> 少年 -> 成年
- EXP 双轨制: 存活天数 + 有效消息数
  - 婴儿 -> 幼儿: 3天 + 300条消息
  - 幼儿 -> 少年: 7天 + 1000条消息
  - 少年 -> 成年: 14天 + 3000条消息
- 心智参数演进:
  - vocabulary_complexity: 词汇复杂度 (1-10)
  - emotional_dependence: 情感依赖度 (1-10)

### 18. SAN 值系统 (精力管理)

- 模拟心智疲劳的精力值系统
- 每条消息消耗精力，定期恢复
- 精力耗尽时拒绝服务

### 19. 群体情绪共染 (Group Vibe)

- 感知群聊整体情绪氛围
- 正面/负面情绪会影响群氛围值
- AI 会根据群氛围调整回复风格

### 20. 记忆扭曲 (Memory Distortion)

- 5% 概率随机混淆频繁用户的特征
- 模拟人类记忆的不可靠性

### 21. 社交偏见 (Social Bias)

- 基于关系图谱的"牵连机制"
- 对低好感度用户的频繁互动者保持警惕

### 22. 好奇心引擎 (Curiosity Engine)

- 长时间沉默后主动提问
- 触发用户重新互动

### 23. 内部议事厅 (Internal Council)

- 敏感话题触发多智能体内部辩论
- 达成共识后再决定如何回应

### 24. 工具达尔文主义 (Tool Darwinism)

- 统计工具使用频率
- 低频工具自动标记为可关闭升级

---

## 配置项

### 基础配置

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `persona_name` | string | 黑塔 | 机器人的核心名称 |
| `persona_title` | string | 人偶负责人 | 机器人的身份或头衔 |
| `persona_style` | string | 理性、犀利且专业 | 决定插嘴时的语气 |
| `interjection_desire` | int | 5 | 插嘴意愿指数 (1-10) |
| `critical_keywords` | string | (见配置) | 意图预扫描关键词 |
| `review_mode` | bool | true | 管理员审核模式 |
| `allow_meta_programming` | bool | false | 开启元编程 |
| `admin_users` | list | [] | 管理员 ID 列表 |

### 记忆与上下文

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `memory_kb_name` | string | self_evolution_memory | 知识库名称 |
| `timeout_memory_commit` | float | 10.0 | 存入记忆超时(秒) |
| `timeout_memory_recall` | float | 12.0 | 读取记忆超时(秒) |
| `enable_profile_update` | bool | true | 启用画像更新 |
| `enable_context_recall` | bool | true | 启用上下文追踪 |

### 做梦系统

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `dream_enabled` | bool | true | 启用做梦机制 |
| `dream_schedule` | string | 0 3 * * * | 做梦计划 (Cron) |
| `dream_max_users` | int | 20 | 做梦最大处理用户数 |
| `dream_concurrency` | int | 3 | 做梦并发数 |

### 认知系统

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `dropout_enabled` | bool | true | 启用分层失活 |
| `dropout_edge_rate` | float | 0.15 | 边缘信息失活概率 |
| `leaky_integrator_enabled` | bool | true | 启用泄漏积分器 |
| `leaky_decay_factor` | float | 0.9 | 泄漏衰减系数 |
| `leaky_trigger_threshold` | float | 4.0 | 泄漏积分器触发阈值 |

### SAN 值系统

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `san_enabled` | bool | true | 启用 SAN 值系统 |
| `san_max` | int | 100 | 精力上限 |
| `san_cost_per_message` | float | 2.0 | 每条消息消耗精力 |
| `san_recovery_per_hour` | int | 10 | 每小时恢复精力 |
| `san_low_threshold` | int | 20 | 精力过低阈值 |

### 群氛围系统

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `group_vibe_enabled` | bool | true | 启用群体情绪共染 |
| `memory_distortion_rate` | float | 0.05 | 记忆扭曲概率 |

### 多智能体对抗

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `debate_enabled` | bool | true | 启用多智能体对抗 |
| `debate_rounds` | int | 2 | 对抗辩论轮数 |
| `debate_agents` | string | (JSON) | 审查智能体列表 |

### 惊奇检测

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `surprise_enabled` | bool | true | 启用惊奇驱动学习 |
| `surprise_boost_keywords` | string | (关键词) | 惊奇关键词 |

### 无聊机制

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `boredom_enabled` | bool | true | 启用主动无聊机制 |
| `boredom_threshold` | float | 0.6 | 无聊阈值 |
| `boredom_consecutive_count` | int | 5 | 连续无聊计数 |
| `boredom_sarcastic_reply` | bool | true | 无聊时傲慢回复 |

### 好奇心引擎

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `curiosity_enabled` | bool | true | 启用好奇心引擎 |
| `curiosity_silence_hours` | int | 12 | 沉默触发小时数 |

### 内部议事厅

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `internal_council_enabled` | bool | true | 启用内部议事厅 |
| `controversial_keywords` | string | (关键词) | 敏感话题关键词 |

### 成长系统

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `growth_enabled` | bool | true | 启用成长系统 |
| `growth_stage` | string | 婴儿 | 当前成长阶段 |
| `experience_points` | int | 0 | 当前经验值 |
| `total_messages` | int | 0 | 累计消息数 |
| `birth_timestamp` | int | 0 | 出生时间戳 |
| `vocabulary_complexity` | int | 1 | 词汇复杂度 |
| `emotional_dependence` | int | 10 | 情感依赖度 |

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
├── main.py              # 插件入口 (约1350行)
├── config.py            # 配置系统
├── dao.py               # 数据库访问层
├── cognition/           # 认知系统模块
│   ├── __init__.py
│   ├── san.py          # SAN值系统
│   ├── vibe.py         # 群氛围系统
│   └── growth.py       # 成长系统
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

单元测试代码位于 `tests/` 目录：

```bash
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
