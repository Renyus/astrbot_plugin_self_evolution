# [SYSTEM_CORE] 自我进化 (Self-Evolution) 插件 (v3.0.0: CognitionCore 3.0)

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)

这是一个面向 AstrBot 深度定制的高阶 AI 心智模型插件。经过长达 24 轮深度的底层架构重构与安全对抗测试，它赋予了大语言模型在对话中**自我迭代反思**、**跨会话长期记忆**、**动态技能管理**，乃至最高级的 **代码级自我元编程** 能力。

> ⚠️ **非商业性使用声明 (Non-Commercial Use Only)**: 本项目采用 [CC BY-NC 4.0 (知识共享署名-非商业性使用 4.0 国际许可协议)](LICENSE) 进行授权。任何人均可自由学习、修改和分发本源代码，但**严禁将本插件或其衍生版本用于任何商业盈利目的**（包括但不限于：付费售后、闭源售卖、企业级部署收费等）。

## /// 核心理念与功能特性 (ARCHITECTURE)

### 一、 核心人格进化 (Persona Evolution)
当用户对 AI 的回复表现出强烈的情绪（不满或赞赏）时，AI 能结合预设的**核心普世价值观 (Core Principles)** 进行辩证自省：
- 若反馈有客观建设性：AI 将主动调用 `evolve_persona` 工具提出对自己系统设定 (System Prompt) 的修改建议。
- 若反馈违背底线：AI 将坚守原则边界并优雅拒绝。
- **强制自省**：每日凌晨通过 Cron 调度，或由用户指令隐式触发，进行深度对话复盘。

### 二、 长期记忆库检索 (Long-Term Memory RAG)
基于知识库组件（如 Milvus / Chroma），彻底解决模型“转身就忘”的问题：
- **`commit_to_memory`**：当 AI 侦测到用户的偏好、重要约定事实时，自动将这部分内容存入长期向量记忆节点。
- **`recall_memories`**：遇到相关话题时，AI 可自主利用该工具跨时空检索并提取过往记忆，实现类似人类的回忆机制。

### 三、 元级编程防线 (Meta-Programming Sandboxed)
赋予 AI “阅读并修改自身源码”的科幻级能力（需在管理面板高危解锁）：
- **`get_plugin_source`**：AI 能够读取自己运行所在的 `main.py` 底层源代码。
- **`update_plugin_source`**：AI 能够编写代码迭代升级方案。
- **安全沙箱防线**：生成的代码不会立刻热执行污染宿主，而是由一套防 DoS、防反射的混合 AST（抽象语法树）防线过滤后，隔离下放至 `code_proposals` 目录下，等待人类审查官的肉眼复审。

### 四、 动态技能管理 (Dynamic Tools Management)
大模型可以自主管理和感知自己的能力边界：
- **`list_tools`**：列出目前环境中所有可用的工具节点。
- **`toggle_tool`**：根据复杂任务流需要，自主开关、停用或者启用特定工具流。

### 五、 情感拦截与主动插嘴 (CognitionCore 3.0)
黑塔不再只是一个被动应答的工具，而是具有“好感度矩阵”和“环境观察”能力的实体：
- **情感矩阵 (Emotional Firewall)**：当用户出言不逊，大语言模型会调用 `update_affinity` 静默扣除积分。当积分 $\le 0$ 时，底层逻辑将在 `on_llm_request` 阶段实施**零消耗代码级物理熔断**，拒绝为其提供算力。
- **环境监听与主动插嘴 (Active Eavesdropping)**：黑塔会在内存池中默默“偷听”群聊内容（屏蔽已熔断用户）。当积满一定阈值，后台静默评估该话题是否具有价值。如果有，黑塔将不请自来，突然在群里发出无情嘲讽或提供硬核协助。

---

## /// 部署与配置指南 (CONFIGURATION)

无论是在初次安装还是后续更新中，您只需要在 AstrBot 配置中心找到该插件，根据以下字段进行调节：

| 配置名称 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| **`review_mode`** | `bool` | `true` | **管理员审核模式**。开启后，大模型的进化申请（Prompt 修改）会进入挂起队列，必须由管理员批准才能应用到系统。 |
| **`allow_meta_programming`** | `bool` | `false` | **开启元编程 (危险)**。赋予大模型查看和提议修改自身 `.py` 源码的极高权限。*请务必配合强力代码复审使用！* |
| **`memory_kb_name`** | `string` | `self_evolution_memory` | 用于存储长期记忆碎片的知识库名称，底层自动对齐 RAG 架构。 |
| **`reflection_schedule`** | `string` | `0 2 * * *` | 每日后台静默系统复盘反思计划（Cron 表达式），默认每天凌晨两点进行。 |
| **`core_principles`** | `string` | （见默认文本） | **机器人核心锚点**。即使发生进化，模型也必须死守的道德防线与服务态度底线，防止被恶意用户诱导。 |
| **`admin_users`** | `list` | `[]` | **超级管理员 QQ/ID 列表**。允许执行 `/review_evolutions`, `/approve_evolution` 等高危权限指令的人员，严格遵守 Fail-Safe 默认防御机制。 |
| **`timeout_memory_commit`** | `float` | `10.0` | **存入记忆超时(秒)**。针对弱网环境的存储软超时防护，防止长时间挂起阻塞系统。 |
| **`timeout_memory_recall`** | `float` | `12.0` | **读取记忆超时(秒)**。同上。 |
| **`buffer_threshold`** | `int` | `8` | **主动自省：触发阈值(条)**。非艾特状态下，记忆缓冲池积攒多少条群聊消息后触发一次 LLM 后台自省和插嘴评估。设置过小极耗 Tokens。 |
| **`max_buffer_size`** | `int` | `20` | **主动自省：缓冲池硬上限**。防止水群导致内存溢出。超过此长度的久远零散对话将被丢弃。 |

---

## /// 管理员操作指令 (COMMAND_INTERFACE)

作为 `admin_users` 列表中的超级管理员，您可以通过向机器人发送指令来监管其“进化路线”：

- `/reflect`：**强制触发**一次即时的自我反思流程。机器人会回顾最近对话并思考是否需要进化。
- `/review_evolutions [页码]`：**分页查看**当前由 AI 提出、正在等待您审批的人格进化请求队列。
- `/approve_evolution [请求ID]`：**批准**某项进化审核，人格设定（System Prompt）将立即获得更新。
- `/reject_evolution [请求ID]`：**拒绝**该进化提议，并将其从审核队列中永久移除。
- `/clear_evolutions`：**一键清空**所有待审核的进化请求，用于快速清理不再需要的历史提案。
- `/affinity`：**查看好感度**。查询个人在当前系统内积累的实时好感度评分与系统判定阵营。

### 提示：关于元编程代码提议
如果开启了元编程且 AI 成功通过了所有的层级风控，新代码将会被安全停驻在您的 `AstrBot/data/self_evolution/code_proposals/` 目录中。
**由于高级 Python 魔术方法的不可测性，系统明确拒绝直接热部署。管理员必须人工比对后手动覆盖源码。**
