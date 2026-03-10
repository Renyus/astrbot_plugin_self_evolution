# Self-Evolution 插件技术文档

版本: 4.0.1 (多智能体+关系图谱版)

---

## 0. 核心设计理念：认知卸载 (Cognitive Offloading)

**核心思想**: 把 CPU 干的脏活全扔给晚上的大模型，把白天的毫秒级响应还给代码。

### 架构对比

| 维度 | 旧架构 (3.7.x) | 新架构 (3.8.x) |
|------|----------------|----------------|
| 画像更新 | 实时 LLM 提取 | 凌晨批量总结 |
| 画像存储 | JSON 结构化 | Markdown 文本 |
| 记忆检索 | 实时向量检索 | AI 主动调用 |
| 插嘴评估 | 复杂 Prompt | 精简版 |
| 响应延迟 | 秒级 | 毫秒级 |

### 做梦机制

凌晨 3 点定时任务会：
1. 拉取过去 24 小时对话
2. 按用户分组
3. 调用 LLM 总结为 Markdown 笔记
4. 覆写画像文件

---

## 1. 项目概述

这是一个面向 AstrBot 的 AI 心智模型插件，赋予大语言模型以下能力：

- **自我迭代反思**: 基于用户反馈进行人格进化
- **跨会话长期记忆**: 基于知识库的 RAG 系统
- **动态技能管理**: 自主开关工具
- **代码级自我元编程**: AI 可以读取和提议修改自身源码

---

## 2. 架构设计

### 2.1 模块结构

```
self_evolution/
├── main.py              # 插件入口，事件处理中枢
├── dao.py               # 数据库访问层 (SQLite)
└── engine/
    ├── eavesdropping.py  # 插嘴引擎 (被动监听)
    ├── memory.py        # 记忆管理 (RAG)
    ├── persona.py       # 人格进化管理
    ├── profile.py       # 用户画像管理
    ├── meta_infra.py   # 元编程基础设施
    └── graph.py         # 关系图谱 RAG
```

### 2.2 核心数据流

```
用户消息
    │
    ├─► on_llm_request (拦截层)
    │    ├─► 情感矩阵检查 (affinity <= 0 熔断)
    │    ├─► 上下文注入 (身份、群组、引用)
    │    ├─► 记忆检索注入 (auto_recall)
    │    ├─► 画像注入 (profile)
    │    ├─► 突发偏好检测
    │    └─► 惊奇驱动检测 (Surprise Detection)
    │
    └─► on_message_listener (监听层)
         ├─► 缓冲清理 (内存管理)
         ├─► 自动学习触发 (auto_learn_trigger)
         ├─► 关系图谱记录 (GraphRAG)
         └─► 插嘴评估 (eavesdropping)
```

---

## 3. 核心组件

### 3.1 DAO 层 (dao.py)

**职责**: 数据库访问封装

**数据库表**:
- `pending_evolutions`: 待审核的进化请求
- `pending_reflections`: 待执行的反思标记
- `user_relationships`: 用户好感度矩阵

**关键特性**:
- WAL 模式 SQLite
- 连接存活检测与自动重连
- 读写锁分离
- 重试装饰器 `@with_db_retry`

### 3.2 插嘴引擎 (eavesdropping.py)

**职责**: 被动监听群聊，决定是否主动插话

**触发条件**:
1. 兴趣关键词命中 (`critical_keywords`)
2. 用户 @ 机器人
3. 缓冲池达到阈值 (`buffer_threshold`)

**评估流程**:
```
收集对话片段
    │
    ▼
LLM 决策 (是否插嘴)
    │
    ├─► [IGNORE] → 不插话
    │
    └─► [COMMENT] → 检查元评论过滤
         │
         ├─► 通过 → yield 回复
         │
         └─► 拦截 → 不插话
```

**元评论过滤**: 防止 LLM 输出类似"监控显示..."的系统报告

### 3.3 记忆管理 (memory.py)

**职责**: 长期记忆的存储与检索

**存储策略**:
- 群聊: `memory_group_{群号}_user_{用户ID}`
- 私聊: `memory_user_{用户ID}`
- 群公共: `group_memory_{群号}`

**自动学习触发**:
- @ 机器人
- 关键词命中
- 告别语 (再见、晚安等)
- 表达偏好 (我喜欢、我讨厌等)

**核心方法**:
- `auto_recall_inject`: 自动检索并注入上下文
- `commit_to_memory`: 手动存入记忆
- `recall_memories`: 检索记忆
- `_do_commit_memory`: 实际写入 (含去重、容量清理)

### 3.4 用户画像 (profile.py)

**职责**: 维护用户印象笔记 (Markdown 格式)

**存储格式**: Markdown 文本 (不再是 JSON)
```markdown
# 用户印象笔记

---
**2026-03-10 14:30**
这个用户喜欢讨论技术话题，对 Python 比较感兴趣，说话比较直接。

---
**2026-03-09 09:15**
今天在群里讨论了模拟宇宙的相关内容，用户表现出对游戏剧情的兴趣。
```

**精度模式**:
- `simple`: Markdown 文本摘要 (默认)
- `detailed`: 结构化标签 (开发中)

**存储**: 本地 Markdown 文件 (`data/profiles/user_{id}.md`)

### 3.5 人格进化 (persona.py)

**职责**: 管理 AI 人格进化请求

**流程**:
1. AI 调用 `evolve_persona(new_system_prompt, reason)`
2. 审核模式: 进入待审核队列
3. 非审核模式: 直接应用
4. 管理员批准后: 调用 `persona_manager.update_persona()`

### 3.6 元编程 (meta_infra.py)

**职责**: AI 读取/修改自身源码

**安全机制**:
- AST 语法树校验
- 危险模块/函数黑名单
- 文件大小限制 (100KB)
- 代码隔离存储 (不直接执行)
- 管理员人工审查

**危险检测**:
- 禁止导入: subprocess, shutil, socket, urllib, requests, ctypes, builtins
- 禁止调用: eval, exec, __import__, compile
- 禁止访问: __bases__, __subclasses__, __mro__, __globals__ 等

### 3.7 惊奇驱动学习 (Surprise Detection)

**职责**: 检测用户认知颠覆/惊喜表达，触发即时画像更新

**核心思想**: 预测编码 (Predictive Coding) - 只记忆出乎意料的事情

**触发条件**: 用户消息包含惊奇关键词
- "我错了"、"原来如此"、"没想到"
- "居然"、"竟然"、"震惊"
- "牛逼"、"绝了"、"笑死" 等

**处理流程**:
```
用户消息
    │
    ▼
检测惊奇关键词
    │
    ├─► 未命中 → 正常处理
    │
    └─► 命中 → 注入画像更新提示
              │
              ▼
         LLM 主动调用 update_user_profile
              │
              ▼
         记录认知颠覆时刻
```

**配置项**:
- `surprise_enabled`: 是否启用
- `surprise_boost_keywords`: 惊奇关键词列表

### 3.8 关系图谱 (graph.py)

**职责**: 维护用户关系网络，用于增强 RAG 检索

**存储**: JSON 文件 (`data/graph/user_relations.json`)

**核心功能**:
- `record_interaction`: 记录用户在群中的互动
- `get_user_groups`: 获取用户所在群组
- `get_frequent_interactors`: 获取频繁互动用户
- `find_common_groups`: 查找两个用户的共同群
- `enhance_recall`: 关系图谱增强的记忆检索
- `cleanup_stale_nodes`: 清理 90 天未活跃节点

**数据模型**:
```json
{
  "user_id": {
    "user_id": "123456",
    "groups": ["群号1", "群号2"],
    "interactions": {"用户A": 10, "用户B": 5},
    "last_seen": "2026-03-10T10:30:00",
    "traits": ["技术宅", "夜猫子"]
  }
}
```

**自动清理**: 90 天未活跃的节点会被清理

---

## 4. 事件处理

### 4.1 on_llm_request

每次 LLM 请求前执行:
1. 检查用户好感度，<= 0 则熔断 (最前置)
2. 提取引用/At 信息
3. 注入上下文 (身份、群组)
4. 注入反思指令 (如果有待处理)
5. 注入核心锚点
6. ~~自动记忆检索注入~~ (已移除，改为 AI 主动调用)
7. 用户画像注入 (Markdown 文本直接拼接)
8. 突发偏好检测 (用户表达偏好变化)
9. **惊奇驱动检测** (Surprise Detection, 4.0.1 新增)
10. 交流准则注入

### 4.2 on_message_listener

每条消息到达时执行:
1. 清理过期缓冲 (每5分钟)
2. 触发自动学习
3. **关系图谱记录** (GraphRAG, 4.0.1 新增)
4. 转发给插嘴引擎

### 4.3 定时任务

- **每日自省 + 做梦** (`_scheduled_reflection`): 
  - 默认凌晨 3 点
  - 设置反思标志位
  - 执行"大赦天下" (恢复负面用户好感度)
  - **批量处理用户画像** (3.8.0 新增)
   
- **画像清理** (`_scheduled_profile_cleanup`):
  - ~~每天凌晨 4 点~~ (已废弃，Markdown 格式无需清理)

---

## 5. 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| persona_name | string | 黑塔 | 机器人名称 |
| persona_title | string | 人偶负责人 | 机器人身份 |
| persona_style | string | 理性、犀利且专业 | 插嘴风格 |
| interjection_desire | int | 5 | 插嘴意愿 (1-10) |
| critical_keywords | string | (见配置) | 触发关键词 (正则) |
| review_mode | bool | true | 审核模式 |
| allow_meta_programming | bool | false | 开启元编程 |
| memory_kb_name | string | self_evolution_memory | 知识库名称 |
| reflection_schedule | string | 0 3 * * * | 自省 Cron |
| core_principles | string | (见配置) | 核心价值观 |
| admin_users | list | [] | 管理员列表 |
| buffer_threshold | int | 8 | 触发阈值 |
| max_buffer_size | int | 20 | 缓冲上限 |
| enable_profile_update | bool | true | 启用画像 |
| enable_context_recall | bool | true | 启用上下文追踪 |
| profile_precision_mode | string | simple | 画像精度模式 |
| dream_enabled | bool | true | 启用做梦机制 |
| dream_schedule | string | 0 3 * * * | 做梦 Cron |
| dream_max_users | int | 20 | 做梦最大处理用户数 |
| dream_concurrency | int | 3 | 做梦并发数 |
| prompt_meltdown_message | string | (见配置) | 熔断提示词 |
| prompt_reflection_instruction | string | (见配置) | 反思指令 |
| prompt_anchor_injection | string | (见配置) | 核心锚点注入 |
| prompt_communication_guidelines | string | (见配置) | 交流准则 |
| prompt_eavesdrop_system | string | (见配置) | 插嘴系统提示词 |
| prompt_dream_user_summary | string | (见配置) | 做梦-用户画像总结 |
| prompt_dream_user_system | string | (见配置) | 做梦-用户画像系统提示词 |
| prompt_dream_group_summary | string | (见配置) | 做梦-群记忆总结 |
| prompt_dream_group_system | string | (见配置) | 做梦-群记忆系统提示词 |
| debate_enabled | bool | true | 启用多智能体对抗 |
| debate_rounds | int | 2 | 对抗辩论轮数 |
| debate_system_prompt | string | (见配置) | 审查 Agent 系统提示词 |
| debate_criteria | string | (见配置) | 代码审查标准 |
| surprise_enabled | bool | true | 启用惊奇驱动学习 |
| surprise_boost_keywords | string | (见配置) | 惊奇关键词 |
| graph_enabled | bool | true | 启用关系图谱 RAG |

---

## 6. LLM 工具

| 工具 | 功能 |
|------|------|
| commit_to_memory | 存入长期记忆 |
| recall_memories | 检索记忆 |
| learn_from_context | 从对话提取记忆 |
| clear_all_memory | 清空记忆 |
| list_memories | 列出记忆 |
| delete_memory | 删除单条记忆 |
| auto_recall | 主动注入记忆 |
| save_group_knowledge | 保存群公共知识 |
| get_user_profile | 获取用户画像 |
| update_user_profile | 更新用户画像 (参数已简化为 content) |

---

## 8. 工具参数变更 (3.8.0)

### update_user_profile

| 参数 | 旧版 | 新版 (3.8.0) |
|------|------|---------------|
| tags | string (逗号分隔) | 已移除 |
| traits | string (逗号分隔) | 已移除 |
| reason | string | 已移除 |
| content | - | string (印象描述文本) |

**新调用示例**:
```python
update_user_profile(target_user_id="123456", content="这个用户喜欢讨论技术问题，说话比较直接")
```
| update_affinity | 调整好感度 |
| evolve_persona | 进化人格 |
| list_tools | 列出工具 |
| toggle_tool | 开关工具 |
| get_plugin_source | 读取源码 |
| update_plugin_source | 提议修改源码 |
| get_user_messages | 获取用户历史消息 |

---

## 7. 管理员指令

| 指令 | 功能 |
|------|------|
| /reflect | 触发自我反思 |
| /review_evolutions | 查看待审核进化 |
| /approve_evolution | 批准进化 |
| /reject_evolution | 拒绝进化 |
| /clear_evolutions | 清空进化队列 |
| /set_affinity | 手动调整好感度 |
| /affinity | 查看好感度 |
| /view_profile | 查看用户画像 |
| /delete_profile | 删除画像 |
| /profile_stats | 画像统计 |
| /graph_info | 查看用户关系图谱 |
| /graph_stats | 查看群关系图谱统计 |

---

## 8. 潜在问题与改进点

### 8.1 性能问题

1. **LLM 调用无节流**: 单条消息可能触发多次 LLM 调用
2. **缓冲池清理频率**: 每5分钟清理一次可能不够
3. **画像提取 LLM 调用**: 每次关键场景都调用 LLM 提取标签

### 8.2 架构问题

1. **critical_keywords 重复定义**: main.py 和 memory.py 各自读取
2. **未使用变量**: memory.py 中的 `group_id` (行 184)
3. **缺少 busy_timeout**: SQLite WAL 模式未设置

### 8.3 功能问题

1. **画像更新未调用**: `update_profile_from_dialogue` 定义但未在主流程中调用
2. **_session_speakers 未清理**: 会话结束后 speaker map 一直存在
3. **get_user_messages API**: 参数 `user_id=group_id` 语义存疑

### 8.4 安全问题

1. **元编程绕过风险**: AST 检查可以被复杂反射绕过
2. **代码执行**: 虽有审核机制，但生成的代码可能仍含漏洞
3. **好感度滥用**: 管理员可以任意修改用户好感度

### 8.5 代码质量问题

1. **未使用导入**: `inspect` 模块导入但未使用 (main.py)
2. **版本号**: 代码中为 3.7.0
3. **重复代码**: persona.py 和 main.py 中都有权限检查逻辑

---

## 9. 数据存储

### 9.1 SQLite 数据库
- 位置: `data/self_evolution/self_evolution.db`
- 表: pending_evolutions, pending_reflections, user_relationships

### 9.2 用户画像
- 位置: `data/self_evolution/profiles/user_{id}.md`
- 格式: Markdown

### 9.3 关系图谱
- 位置: `data/graph/user_relations.json`
- 格式: JSON

### 9.4 代码提案
- 位置: `data/self_evolution/code_proposals/`
- 格式: `*.proposal`

---

## 10. 依赖

- astrbot (框架)
- aiosqlite (异步 SQLite)
- asyncio (内置)
- datetime (内置)
- json (内置)
- pathlib (内置)
- re (内置)
- logging (内置)

---

## 11. 测试

单元测试代码位于 `test` 分支：

```bash
git checkout test
pip install pytest pytest-asyncio
pytest tests/ -v
```

**测试覆盖**:
- DAO 层: 8 tests
- 画像模块: 7 tests
- 记忆模块: 8 tests
- 插嘴引擎: 5 tests
- 人格进化: 4 tests
- 元编程: 4 tests
- 定时任务: 3 tests
- LLM 工具: 3 tests
- 管理员指令: 4 tests

**总计**: 46 tests
