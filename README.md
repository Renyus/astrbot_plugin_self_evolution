# 自我进化 (Self-Evolution) 插件

版本: 5.0.0

交流群: 1087272376 (self_evolution插件交流反馈群)

---

## 简介

这是一个面向 AstrBot 的 AI 心智模型插件，赋予大语言模型自我迭代反思、长期记忆、用户认知等高级能力。

---

## 核心架构

### 消息流转

```
消息进入
    ↓
┌─────────────────────────────────────────┐
│ 过滤门（Filter Gates）                  │
├─────────────────────────────────────────┤
│ 1. 命令前缀过滤 (/, . 等)               │
│ 2. 好感度检查 (score <= 0 拦截)        │
│ 3. 消息过短过滤 (<6字)                 │
│ 4. 信息熵过滤 (表情/重复内容)          │
│ 5. 无聊检测 (低信息量累积)             │
│ 6. 关键词预扫描 (黑塔/空间站等)        │
└─────────────────────────────────────────┘
    ↓ 通过过滤门后
┌─────────────────────────────────────────┐
│ 漏斗机制（Funnel） - 用户活跃判定       │
├─────────────────────────────────────────┤
│ L1: @机器人 | 命令前缀 | 引用回复       │
│ L2: 唤醒词(黑塔) | AI意图句式          │
│ L3: 30秒活跃时间窗口                   │
└─────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────┐
│ 插嘴判定                                 │
├─────────────────────────────────────────┤
│ - @机器人 → 强制触发                    │
│ - 关键词命中 → 强制触发                  │
│ - 泄漏积分器 (指数衰减模型)             │
└─────────────────────────────────────────┘
    ↓
LLM 决策 (插嘴 / IGNORE)
```

### 指数衰减积分器

使用时间衰减模型模拟更自然的插嘴节奏：

```
S_t = S_{t-1} * e^(-λ*Δt) + w_i

S_t: 当前插嘴冲动值
S_{t-1}: 上次积分值
λ: 衰减系数 (控制冷静速度)
Δt: 距离上次发言的时间(分钟)
w_i: 当前消息权重
```

---

## 核心功能

### 1. 用户画像系统

- 漏斗机制标记活跃用户
- 定时任务(凌晨)构建画像
- 群号白名单过滤
- 分层失活(核心信息永不丢失)
- 消息清洗(去重+长度过滤)

### 2. 长期记忆

- commit_to_memory / recall_memories
- 知识库存储 (Chroma/Milvus)
- 关系图谱 RAG

### 3. SAN 值系统

- 模拟心智疲劳
- 每条消息消耗精力，定期恢复
- 精力耗尽时拒绝服务

### 4. 群体情绪共染

- 感知群聊整体情绪
- 影响 AI 回复风格

### 5. 元级编程 (可选)

- 读取/修改插件源码
- 安全沙箱等待人工审核

---

## 配置项

### 基础配置

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `persona_name` | string | 黑塔 | 机器人名称 |
| `persona_title` | string | 人偶负责人 | 机器人身份 |
| `interjection_desire` | int | 5 | 插嘴意愿 (1-10) |
| `critical_keywords` | string | (见配置) | 关键词预扫描 |
| `review_mode` | bool | true | 审核模式 |

### 用户画像

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `enable_profile_update` | bool | true | 启用画像更新 |
| `profile_group_whitelist` | string | "" | 画像构建群白名单 |

### 积分器

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `leaky_integrator_enabled` | bool | true | 启用泄漏积分器 |
| `leaky_decay_factor` | float | 0.9 | 衰减系数 |
| `leaky_trigger_threshold` | float | 4.0 | 触发阈值 |

### SAN 值

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `san_enabled` | bool | true | 启用 SAN 值系统 |
| `san_max` | int | 100 | 精力上限 |
| `san_cost_per_message` | float | 2.0 | 每条消息消耗 |

### 群氛围

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `group_vibe_enabled` | bool | true | 启用群体情绪共染 |

---

## 管理员指令

- `/reflect` - 强制自省
- `/set_affinity [用户ID] [分数]` - 修正好感度
- `/affinity` - 查看自己好感度
- `/view_profile [用户ID]` - 查看用户画像
- `/delete_profile [用户ID]` - 删除画像

---

## LLM 工具

| 工具名称 | 说明 |
| :--- | :--- |
| `commit_to_memory` | 存入记忆 |
| `recall_memories` | 检索记忆 |
| `get_user_profile` | 获取画像 |
| `update_user_profile` | 更新画像 |
| `update_affinity` | 调整好感度 |
| `evolve_persona` | 修改人格 |

---

## 自定义提示词

提示词集中在 `prompts.yaml` 文件中管理：

```bash
self_evolution/prompts.yaml
```

修改后重启 AstrBot 生效。

---

## 目录结构

```
self_evolution/
├── main.py              # 插件入口
├── config.py           # 配置系统
├── prompts.yaml        # 提示词配置
├── prompts.py          # 提示词加载器
├── dao.py              # 数据库层
├── cognition/          # 认知系统
│   ├── san.py         # SAN值
│   └── vibe.py        # 群氛围
├── engine/
│   ├── eavesdropping.py  # 插嘴引擎
│   ├── memory.py        # 记忆管理
│   ├── persona.py      # 人格进化
│   ├── profile.py     # 用户画像
│   ├── meta_infra.py  # 元编程
│   └── graph.py       # 关系图谱
└── tests/             # 单元测试
```

---

## 测试

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

---

## 开源协议

CC BY-NC 4.0 - 署名-非商业性使用
