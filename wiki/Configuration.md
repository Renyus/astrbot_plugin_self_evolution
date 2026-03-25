# 配置说明

> 从"参考手册"到"配置指南"，快速找到适合你的配置方案

---

## 配置分组详解

### 基础配置（base）

控制插件的整体行为。

| 配置项 | 默认值 | 说明 | 建议 |
|--------|--------|------|------|
| `persona_name` | 黑塔 | 机器人名字 | ⚠️ **必改** |
| `admin_users` | [] | 额外管理员列表 | 需要多人管理时填写 |
| `target_scopes` | [] | 任务处理范围 | 留空自动发现 |
| `review_mode` | true | 人格进化审核。危险操作需开启 | 建议保持开启 |

**使用场景示例**：

```
场景：希望多个用户能管理插件
配置：admin_users: ["123456789", "987654321"]

场景：只想在特定群使用插件
配置：target_scopes: ["123456789"]
```

---

### 记忆与总结（memory_summary）

控制长期记忆和每日总结功能。

| 配置项 | 默认值 | 说明 | 建议 |
|--------|--------|------|------|
| `memory_enabled` | true | 启用长期记忆 | ⚠️ **核心功能，建议开启** |
| `memory_kb_name` | self_evolution_memory | 知识库名称 | 需与创建的知识库一致 |
| `memory_summary_schedule` | 0 3 * * * | 每日总结时间 | 默认凌晨3点 |
| `enable_kb_memory_recall` | true | AI回复时召回记忆 | 建议开启 |

**使用场景示例**：

```
场景：希望每天早上8点生成昨日总结
配置：memory_summary_schedule: "0 8 * * *"

场景：已经有一个知识库，想复用
配置：memory_kb_name: "my_existing_kb"
```

---

### 用户画像（profile）

控制用户画像的创建和更新。

| 配置项 | 默认值 | 说明 | 建议 |
|--------|--------|------|------|
| `enable_profile_injection` | true | 回复时注入画像 | ⚠️ **建议开启** |
| `auto_profile_enabled` | true | 自动创建画像 | 建议开启 |
| `profile_msg_count` | 500 | 分析消息条数 | 默认值即可 |
| `profile_cooldown_minutes` | 10 | 更新冷却时间 | 默认值即可 |

**使用场景示例**：

```
场景：只想给特定用户建档
配置：auto_profile_enabled: false
然后手动使用 /profile create 命令

场景：希望画像更新更频繁
配置：profile_cooldown_minutes: 5
```

---

### 社交参与（engagement）

控制机器人主动插嘴的行为。

| 配置项 | 默认值 | 说明 | 建议 |
|--------|--------|------|------|
| `interject_enabled` | false | 启用主动插嘴 | 想机器人更主动则开启 |
| `interject_interval` | 30 | 检查间隔（秒） | 越小越频繁 |
| `interject_cooldown` | 30 | 插嘴冷却（秒） | 防止过于频繁 |
| `interject_min_msg_count` | 10 | 最少新增消息数 | 群里要有足够活跃度 |
| `interject_silence_timeout` | 15 | 最短留白时间（秒） | 消息间隔要够长 |
| `interject_trigger_probability` | 0.5 | 触发概率 | 0-1之间，越大越爱说话 |

**使用场景示例**：

```
场景：希望机器人只在很活跃时才参与
配置：interject_min_msg_count: 20

场景：希望机器人更保守，偶尔才插话
配置：interject_trigger_probability: 0.3

场景：希望机器人话多一点
配置：interject_cooldown: 10
         interject_trigger_probability: 0.7
```

---

### 情感积分（affinity）

控制好感度的自动计算。

| 配置项 | 默认值 | 说明 | 建议 |
|--------|--------|------|------|
| `affinity_auto_enabled` | true | 启用自动好感度 | 建议开启 |
| `affinity_recovery_enabled` | true | 每日自动恢复 | 建议开启 |
| `affinity_direct_engagement_delta` | 1 | @/回复/私聊加分 | 默认值即可 |
| `affinity_friendly_language_delta` | 1 | 礼貌词加分 | 默认值即可 |
| `affinity_hostile_language_delta` | -2 | 攻击词扣分 | 默认值即可 |

**使用场景示例**：

```
场景：希望好感度变化更明显
配置：affinity_direct_engagement_delta: 3
         affinity_hostile_language_delta: -5
```

---

### SAN精力系统（san）

控制机器人的"精力"和疲劳感。

| 配置项 | 默认值 | 说明 | 建议 |
|--------|--------|------|------|
| `san_enabled` | true | 启用SAN系统 | 建议开启 |
| `san_max` | 100 | 精力上限 | 默认值即可 |
| `san_cost_per_message` | 2.0 | 每次消息消耗 | 默认值即可 |
| `san_recovery_per_hour` | 10 | 每小时恢复 | 默认值即可 |
| `san_low_threshold` | 20 | 低精力阈值 | 低于此值会疲劳 |

**使用场景示例**：

```
场景：机器人频繁说"累了"
配置：san_max: 150
         san_cost_per_message: 1.0
         san_recovery_per_hour: 15

场景：希望疲劳感更明显
配置：san_low_threshold: 30
```

---

### 表情包（sticker）

控制表情包功能。

| 配置项 | 默认值 | 说明 | 建议 |
|--------|--------|------|------|
| `entertainment_enabled` | true | 启用娱乐模块 | 建议开启 |
| `sticker_learning_enabled` | false | 学习群友表情包 | 需要指定target_qq |
| `sticker_daily_limit` | 50 | 每日学习上限 | 默认值即可 |
| `sticker_send_cooldown` | 30 | 发送冷却（分钟） | 默认值即可 |

---

### 元编程（meta）

⚠️ **实验性功能**，默认关闭。开启前需要明白自己在做什么。

| 配置项 | 默认值 | 说明 | 建议 |
|--------|--------|------|------|
| `meta_enabled` | true | 启用元编程 | 默认关闭 |
| `allow_meta_programming` | false | 允许代码修改 | 极度危险，谨慎开启 |

---

### 调试（debug）

用于排查问题，非开发者建议关闭。默认关闭。

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `debug_log_enabled` | false | 全局调试日志 |
| `memory_debug_enabled` | false | 记忆模块调试 |
| `engagement_debug_enabled` | false | 社交模块调试 |
| `affinity_debug_enabled` | false | 情感积分调试 |

---

## 配置示例：完整配置

```json
{
  "base": {
    "persona_name": "小助手",
    "admin_users": ["123456789"],
    "target_scopes": [],
    "review_mode": true
  },
  "memory_summary": {
    "memory_enabled": true,
    "memory_kb_name": "self_evolution_memory",
    "memory_fetch_page_size": 500,
    "memory_summary_chunk_size": 200,
    "memory_summary_schedule": "0 3 * * *",
    "enable_kb_memory_recall": true
  },
  "profile": {
    "profile_msg_count": 500,
    "profile_cooldown_minutes": 10,
    "enable_profile_injection": true,
    "enable_profile_fact_writeback": true,
    "auto_profile_enabled": true,
    "auto_profile_schedule": "0 0 * * *",
    "auto_profile_batch_size": 3,
    "auto_profile_batch_interval": 30
  },
  "reflection": {
    "reflection_enabled": true,
    "reflection_schedule": "0 2 * * *"
  },
  "engagement": {
    "interject_enabled": false,
    "interject_interval": 30,
    "interject_cooldown": 30,
    "interject_min_msg_count": 10,
    "interject_silence_timeout": 15,
    "interject_trigger_probability": 0.5,
    "interject_analyze_count": 15,
    "engagement_react_probability": 0.15
  },
  "affinity": {
    "affinity_auto_enabled": true,
    "affinity_recovery_enabled": true,
    "affinity_direct_engagement_delta": 1,
    "affinity_friendly_language_delta": 1,
    "affinity_hostile_language_delta": -2,
    "affinity_returning_user_delta": 1
  },
  "san": {
    "san_enabled": true,
    "san_max": 100,
    "san_cost_per_message": 2.0,
    "san_recovery_per_hour": 10,
    "san_low_threshold": 20,
    "san_auto_analyze_enabled": true
  },
  "sticker": {
    "entertainment_enabled": true,
    "sticker_learning_enabled": false
  },
  "meta": {
    "meta_enabled": true,
    "allow_meta_programming": false
  },
  "debug": {
    "debug_log_enabled": false
  }
}
```

---

## 下一步

- 📋 [命令参考](Commands) - 了解所有可用命令
- 🧠 [记忆系统](Memory-System) - 理解记忆工作原理
- ❓ [故障排查](Troubleshooting) - 配置相关问题解答
