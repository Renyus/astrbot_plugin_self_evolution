# LLM 工具

> **一句话理解**：给机器人配备了"大脑工具"，让它能主动查询记忆、调整策略。

---

## 工具一览

| 类别 | 工具名 | 作用 |
|------|--------|------|
| 记忆 | `get_user_profile` | 了解用户是谁 |
| 记忆 | `get_user_messages` | 查询用户历史发言 |
| 记忆 | `get_group_recent_context` | 了解最近聊了什么 |
| 记忆 | `get_group_memory_summary` | 查询某天的总结 |
| 记忆 | `upsert_cognitive_memory` | 记录重要信息 |
| 情感 | `update_affinity` | 调整好感度 |
| 娱乐 | `list_stickers` | 查看表情包 |
| 娱乐 | `send_sticker` | 发送表情包 |
| 管理 | `list_tools` | 查看工具列表 |
| 管理 | `toggle_tool` | 启用/停用工具 |
| 元编程 | `evolve_persona` | 修改系统提示词 |
| 元编程 | `get_plugin_source` | 读取插件源码 |
| 元编程 | `update_plugin_source` | 修改插件源码 |

---

## 记忆类工具

### get_user_profile

**用途**：获取用户画像，了解用户特征。

**什么时候调用？**
- 回复用户前想了解对方
- 用户问"你知道我是谁吗"

**示例对话**：
```
用户：你知道我喜欢什么吗？
→ 机器人调用 get_user_profile
→ 返回：该用户喜欢火锅、游戏
→ 机器人：你之前说过喜欢吃火锅
```

**返回值**：
- 用户身份信息
- 兴趣爱好
- 行为特征
- 最近变化

---

### get_user_messages

**用途**：查询某用户的历史发言。

**什么时候调用？**
- 用户问"某人以前说过什么"
- 需要验证某人的历史言论

**与类似工具的区别**：

| 工具 | 回答的问题 |
|------|-----------|
| `get_group_recent_context` | "刚刚在聊什么" |
| `get_group_memory_summary` | "昨天聊了什么" |
| `get_user_messages` | "某人以前说过什么" |

**示例对话**：
```
用户：小明以前推荐过什么游戏？
→ 机器人调用 get_user_messages(target_user_id="小明")
→ 搜索历史消息中的游戏推荐
→ 机器人：小明之前推荐过《XXX》
```

---

### get_group_recent_context

**用途**：获取群里最近的消息。

**什么时候调用？**
- 用户问"刚刚在聊什么"
- 机器人刚加入群聊想了解上下文

**示例对话**：
```
用户：@机器人 你知道他们在说什么吗？
→ 机器人调用 get_group_recent_context
→ 返回最近30条消息摘要
→ 机器人：他们在讨论新番...
```

---

### get_group_memory_summary

**用途**：获取某天的群聊总结。

**什么时候调用？**
- 用户问"昨天聊了什么"
- 需要回顾之前的讨论

**参数**：
- `date`: 日期，可选值 `yesterday` / `today` / `YYYY-MM-DD`

**示例对话**：
```
用户：昨天群里聊了什么？
→ 机器人调用 get_group_memory_summary(date="yesterday")
→ 返回昨日总结
→ 机器人：昨天大家主要讨论了...
```

---

### upsert_cognitive_memory

**用途**：统一记忆存储入口。

**什么时候调用？**
- 对话中发现需要记住的重要信息
- 用户明确说"记住这个"

**分类和路由**：

| category | 存储位置 | 示例 |
|----------|---------|------|
| `user_profile` | 用户画像 | "我是个程序员" |
| `user_preference` | 用户画像 | "我喜欢吃辣" |
| `user_trait` | 用户画像 | "我性格比较内向" |
| `session_event` | 知识库 | "我们周五聚会" |

**示例对话**：
```
用户：记住，我是程序员，喜欢打原神
→ 机器人调用 upsert_cognitive_memory
   - category: "user_profile"
   - content: "职业是程序员，喜欢打原神"
→ 机器人：好的，我记住了
```

---

## 情感类工具

### update_affinity

**用途**：根据用户言行调整好感度。

**什么时候调用？**
- LLM判断用户的某个行为应该影响好感度
- 自动弱信号无法覆盖的场景

**参数**：
- `delta`: -20 到 +20 的调整值
- `reason`: 调整理由

**示例对话**：
```
用户：谢谢你帮我！
→ 机器人判断这是礼貌行为
→ 调用 update_affinity(delta=2, reason="用户表达感谢")
→ 机器人：不客气~
```

---

## 娱乐类工具

### list_stickers

**用途**：列出可用表情包。

**参数**：
- `limit`: 返回数量，最大50

---

### send_sticker

**用途**：发送表情包。

**什么时候调用？**
- 机器人想用表情包表达情绪
- 冷场时活跃气氛

**注意**：仅限群聊使用，每次随机发送。

---

## 工具管理类

### list_tools

**用途**：查看所有工具及其状态。

**示例返回**：
```
已注册工具（15个）：
✓ get_user_profile - 已启用
✓ send_sticker - 已启用
✗ evolve_persona - 已禁用
...
```

---

### toggle_tool

**用途**：启用或停用某个工具。

**什么时候使用？**
- 某些工具不适用于当前场景
- 临时禁用可能出问题的工具

**注意**：核心工具（如 toggle_tool 自身）无法停用。

---

## 元编程类工具

### evolve_persona

**用途**：修改机器人的系统提示词。

**什么时候调用？**
- 机器人想调整自己的说话风格
- 用户要求改变机器人的性格

**⚠️ 注意**：如果开启了 `review_mode`，修改需要管理员审核。

---

### get_plugin_source

**用途**：读取插件源码。

**权限**：管理员

**参数**：
- `mod_name`: main / dao / eavesdropping / meta_infra / memory / persona

---

### update_plugin_source

**用途**：提出代码修改建议。

**⚠️ 极度危险**：此工具允许AI修改自己的代码。

**安全限制**：
- 需要管理员权限
- 如果开启 `review_mode`，需要审核
- 如果开启 `debate_enabled`，会经过多轮辩论审查

---

## 工具职责速查表

| 用户问题 | 调用工具 |
|---------|---------|
| "我是谁？" / "你知道我是谁吗？" | `get_user_profile` |
| "他以前说过什么？" | `get_user_messages` |
| "刚刚在聊什么？" | `get_group_recent_context` |
| "昨天聊了什么？" | `get_group_memory_summary` |
| "记住我是..." | `upsert_cognitive_memory` |
| "发个表情包" | `send_sticker` |

---

## 配置相关

工具使用不需要额外配置，但可以通过配置影响工具行为：

```json
{
  "memory_summary": {
    "enable_kb_memory_recall": true  // 影响记忆类工具效果
  },
  "profile": {
    "enable_profile_injection": true  // 影响 get_user_profile
  },
  "meta": {
    "allow_meta_programming": false   // 控制 update_plugin_source
  }
}
```

---

## 下一步

- 🧠 [记忆系统](Memory-System) - 了解记忆如何存储
- 💬 [社交互动](Social-Engagement) - 了解工具在社交中的作用
- 📋 [命令参考](Commands) - 查看手动调用记忆的工具
