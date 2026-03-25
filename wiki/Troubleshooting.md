# 故障排查

> 按问题频率排序，快速找到解决方案

---

## 🔴 最常见问题

### 1. 插件无法加载

**症状**：安装后插件不生效，命令无响应

**快速诊断**：

```bash
# 1. 查看日志中是否有
[SelfEvolution] 核心组件初始化完成

# 2. 如果没有，检查是否有错误信息
[SelfEvolution] 知识库不存在: xxx
```

**解决方案**：

| 错误信息 | 原因 | 解决 |
|---------|------|------|
| 知识库不存在 | 未创建知识库 | 创建名为 `self_evolution_memory` 的知识库 |
| NapCat连接失败 | 消息后端不匹配 | 确保使用 NapCat 作为后端 |
| LLM未配置 | 缺少AI模型 | 在 AstrBot 配置可用的 LLM |

---

### 2. 记忆功能不生效

**症状**：机器人记不住之前的事

**快速诊断步骤**：

```
Step 1: 检查配置
  → memory_enabled = true?
  → enable_kb_memory_recall = true?

Step 2: 开启调试
  → memory_debug_enabled = true

Step 3: 发送测试消息，查看日志
  → 应该有 [MemoryWrite] 记录
  → 应该有 [MemoryInject] 记录
```

**可能原因**：
- 知识库未创建或名称不匹配
- 消息未达到分析阈值
- LLM 未正确返回记忆内容

---

### 3. 主动插嘴不生效

**症状**：机器人不会主动参与对话

**检查清单**：

- [ ] `interject_enabled` 已设置为 `true`
- [ ] 群聊有足够活跃度（默认需要10条新消息）
- [ ] 距离上次插嘴已超过冷却时间（默认30秒）
- [ ] 距离上条消息已超过留白时间（默认15秒）

**快速诊断**：

```
Step 1: 开启调试
  → engagement_debug_enabled = true

Step 2: 在群里连续发送10+条消息

Step 3: 查看日志 [Engagement]
  → 场景判断结果
  → eligibility 检查
  → 概率判定结果
```

**快速测试配置**：
```json
{
  "interject_enabled": true,
  "interject_min_msg_count": 3,
  "interject_silence_timeout": 5,
  "interject_cooldown": 10,
  "interject_trigger_probability": 0.9
}
```

---

## 🟡 常见问题

### 4. 用户画像没有生成

**症状**：`/profile view` 提示画像不存在

**解决步骤**：

```
1. 检查配置
   → enable_profile_injection = true
   → auto_profile_enabled = true

2. 手动创建测试
   → /profile create

3. 检查文件
   → 查看 data/self_evolution/profiles/ 目录
   → 确认有 .json 文件生成

4. 检查日志
   → 开启 debug 查看是否有分析记录
```

---

### 5. SAN 精力耗尽

**症状**：机器人总是说"累了"或拒绝服务

**快速解决**：

```
# 方法1：手动恢复（管理员）
/san set 100

# 方法2：修改配置（永久解决）
{
  "san_max": 150,
  "san_cost_per_message": 1.0,
  "san_recovery_per_hour": 20
}

# 方法3：关闭SAN系统
{
  "san_enabled": false
}
```

---

### 6. 情感积分异常

**症状**：好感度计算不符合预期

**排查步骤**：

```
1. 检查配置
   → affinity_auto_enabled = true

2. 开启调试
   → affinity_debug_enabled = true

3. 触发测试
   → @机器人
   → 查看日志 [Affinity] 是否有信号命中

4. 检查冷却
   → /affinity debug <用户ID>
   → 查看 cooldown 状态
```

---

### 7. 数据库锁定

**症状**：日志中出现 "database is locked"

**说明**：这是 SQLite 高并发下的正常现象

**处理**：
- 框架会自动重试，无需处理
- 如频繁出现，避免同时操作多个群
- 避免频繁重启插件

---

## 🟢 其他问题

### 8. 表情包无法发送

**排查步骤**：

```
1. 检查配置
   → entertainment_enabled = true

2. 检查表情包列表
   → /sticker list

3. 如果没有表情包
   → /sticker add（发送图片后使用）
   → 或开启 sticker_learning_enabled

4. 检查冷却
   → /sticker stats
```

---

### 9. 日志前缀速查

排障时通过这些前缀定位问题：

| 前缀 | 模块 | 排查问题 |
|------|------|---------|
| `[MemoryWrite]` | 记忆写入 | 记忆是否被记录 |
| `[MemoryQuery]` | 记忆查询 | 记忆是否被查询 |
| `[MemorySummary]` | 每日总结 | 总结是否正常生成 |
| `[MemoryInject]` | Prompt注入 | 记忆是否进入对话 |
| `[Engagement]` | 社交参与 | 插嘴逻辑是否正常 |
| `[Affinity]` | 情感积分 | 好感度计算是否正常 |
| `[SAN]` | 精力系统 | SAN相关逻辑 |
| `[Profile]` | 用户画像 | 画像创建/更新 |

---

### 10. 性能问题

**症状**：插件占用资源过高

**优化建议**：

| 配置项 | 默认值 | 建议值 | 影响 |
|--------|--------|--------|------|
| `profile_msg_count` | 500 | 200 | 减少画像分析负担 |
| `interject_interval` | 30 | 60 | 降低检查频率 |
| `san_analyze_interval` | 30 | 60 | 降低氛围分析频率 |
| `memory_fetch_page_size` | 500 | 200 | 减少消息拉取量 |
| `debug_log_enabled` | true | false | 关闭调试输出 |

---

### 11. 数据备份

**备份清单**：

| 数据 | 位置 | 备份方式 |
|------|------|---------|
| 用户画像 | `data/self_evolution/profiles/` | 复制整个文件夹 |
| 数据库 | `data/self_evolution/*.db` | 复制 .db 文件 |
| 表情包 | `data/self_evolution/stickers/` | 复制整个文件夹 |
| 知识库 | AstrBot 知识库管理 | 使用 AstrBot 导出功能 |

---

## 调试配置模板

**排查问题时使用**：

```json
{
  "debug": {
    "debug_log_enabled": true,
    "memory_debug_enabled": true,
    "engagement_debug_enabled": true,
    "affinity_debug_enabled": true
  }
}
```

**生产环境使用**：

```json
{
  "debug": {
    "debug_log_enabled": false,
    "memory_debug_enabled": false,
    "engagement_debug_enabled": false,
    "affinity_debug_enabled": false
  }
}
```

---

## 🆘 仍然无法解决？

1. 加入 QQ 群：`1087272376`
2. 提交 GitHub Issue：https://github.com/Renyus/astrbot_plugin_self_evolution/issues

**提交问题时请提供：**
- 插件版本号（`/system version`）
- AstrBot 版本
- 相关日志片段（注意脱敏）
- 已尝试的解决方法
