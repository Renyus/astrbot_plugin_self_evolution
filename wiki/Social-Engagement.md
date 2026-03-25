# 社交互动系统

> **一句话理解**：机器人会观察群里的氛围，像真人一样决定什么时候说话、说什么。

---

## 社交互动能做什么？

### 场景 1：冷场时活跃气氛

```
群里：...[10分钟没人说话]...
机器人：[发送表情包] 怎么突然安静了
```

### 场景 2：参与讨论

```
群里：
用户A：这个新番你们看了吗？
用户B：看了，剧情一般
用户C：我觉得还可以
机器人：我也看了，画风确实不错，但剧情节奏有点慢
```

### 场景 3：回应@和回复

```
用户：@机器人 你怎么看？
机器人：[根据当前氛围选择回应方式]
```

---

## 参与等级

机器人根据情况选择不同的参与程度：

| 等级 | 说明 | 示例 |
|------|------|------|
| **IGNORE** | 不参与 | 群里在吵架，保持沉默 |
| **REACT** | 轻反应 | 发个表情包或"确实" |
| **BRIEF** | 简短参与 | 发表一句简短评论 |
| **FULL** | 完整参与 | 正常对话回复 |

---

## 群态识别

机器人会自动判断当前群聊状态：

| 状态 | 说明 | 机器人行为 |
|------|------|-----------|
| **IDLE** | 冷场 | 可能主动打破沉默 |
| **CASUAL** | 闲聊 | 偶尔轻反应，偶尔参与 |
| **HELP** | 求助 | 尝试提供帮助 |
| **DEBATE** | 争论 | 可能选择不参与 |

---

## 如何触发主动参与

### 条件清单

以下**所有**条件满足时，机器人才会考虑参与：

- [ ] `interject_enabled` 已开启
- [ ] 距离上次参与已超过 `interject_cooldown` 秒
- [ ] 群里新增了至少 `interject_min_msg_count` 条消息
- [ ] 距离上条消息已超过 `interject_silence_timeout` 秒
- [ ] 通过概率判定（`interject_trigger_probability`）

### 流程图

```
群里有人发言
    │
    ▼
检查冷却时间 ──否──→ 不参与
    │是
    ▼
检查消息数量 ──否──→ 不参与
    │是
    ▼
检查留白时间 ──否──→ 不参与
    │是
    ▼
判断群态（IDLE/CASUAL/HELP/DEBATE）
    │
    ▼
计算参与概率 ──否──→ 不参与
    │是
    ▼
  选择参与等级（REACT/BRIEF/FULL）
    │
    ▼
  执行参与
```

---

## 使用示例

### 示例 1：启用主动参与

```json
{
  "interject_enabled": true,
  "interject_interval": 30,
  "interject_cooldown": 30,
  "interject_min_msg_count": 10,
  "interject_silence_timeout": 15,
  "interject_trigger_probability": 0.5
}
```

### 示例 2：降低门槛（更活跃）

```json
{
  "interject_min_msg_count": 5,      // 5条消息就考虑参与
  "interject_silence_timeout": 5,    // 5秒留白
  "interject_cooldown": 15,          // 15秒冷却
  "interject_trigger_probability": 0.8  // 80%概率参与
}
```

### 示例 3：提高门槛（更保守）

```json
{
  "interject_min_msg_count": 20,     // 20条消息才考虑
  "interject_silence_timeout": 30,   // 30秒留白
  "interject_cooldown": 60,          // 1分钟冷却
  "interject_trigger_probability": 0.3  // 30%概率参与
}
```

---

## 情感积分系统

与社交参与配合的是**情感积分（Affinity）**系统：

### 自动加分场景

| 行为 | 加分 | 说明 |
|------|------|------|
| @机器人 | +1 | 直接互动 |
| 回复机器人 | +1 | 直接互动 |
| 私聊机器人 | +1 | 私聊互动 |
| 使用礼貌词 | +1 | "谢谢"、"厉害"等 |
| 连续回访 | +1 | 连续多日互动 |

### 自动扣分场景

| 行为 | 扣分 | 说明 |
|------|------|------|
| 使用攻击词 | -2 | "滚"、"傻"等 |

### 好感度等级

```
... -10 ───── 0 ───── 20 ───── 50 ───── 100 ...
       陌生   一般    友好     亲近     亲密
```

### 每日恢复

每天自动恢复一定好感度，防止负面印象永久化。

---

## 相关配置

### 社交参与配置

```json
{
  "engagement": {
    "interject_enabled": false,           // 是否启用主动参与
    "interject_interval": 30,             // 检查间隔（秒）
    "interject_cooldown": 30,             // 参与冷却（秒）
    "interject_min_msg_count": 10,        // 最少新增消息数
    "interject_silence_timeout": 15,      // 最短留白时间（秒）
    "interject_trigger_probability": 0.5, // 触发概率
    "interject_analyze_count": 15,        // 分析历史消息数
    "engagement_react_probability": 0.15  // 轻反应概率
  }
}
```

### 情感积分配置

```json
{
  "affinity": {
    "affinity_auto_enabled": true,              // 启用自动积分
    "affinity_recovery_enabled": true,          // 启用每日恢复
    "affinity_direct_engagement_delta": 1,      // 直接互动加分
    "affinity_friendly_language_delta": 1,      // 礼貌词加分
    "affinity_hostile_language_delta": -2,      // 攻击词扣分
    "affinity_returning_user_delta": 1,         // 回访加分
    "affinity_direct_engagement_cooldown_minutes": 360,  // 互动冷却
    "affinity_friendly_daily_limit": 2,         // 礼貌词每日上限
    "affinity_hostile_cooldown_minutes": 60,    // 攻击词冷却
    "affinity_returning_user_daily_limit": 1    // 回访每日上限
  }
}
```

---

## 相关命令

| 命令 | 作用 |
|------|------|
| `/affinity show` | 查看对你的好感度 |
| `/affinity debug <用户ID>` | 查看详细状态（管理员） |
| `/set_affinity <用户ID> <分数>` | 设置好感度（管理员） |

---

## 调试

开启调试日志查看详细决策过程：

```json
{
  "engagement_debug_enabled": true,
  "affinity_debug_enabled": true
}
```

日志前缀：
- `[Engagement]` - 场景判断和参与决策
- `[Affinity]` - 好感度变化记录

---

## 下一步

- 📋 [命令参考](Commands) - 查看好感度相关命令
- 🛠️ [配置说明](Configuration) - 调整参与参数
- ⚡ [SAN系统](SAN-System) - 了解精力如何影响参与
