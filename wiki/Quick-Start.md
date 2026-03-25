# 快速开始

> 从零开始，手把手教你启用插件核心功能

---

## 第一步：安装插件（1分钟）

### 方式A：插件市场安装（推荐）

1. 打开 AstrBot 管理后台
2. 点击左侧菜单「插件」→「插件市场」
3. 搜索 `self_evolution`
4. 点击「安装」

### 方式B：手动安装

```bash
# 进入 AstrBot 插件目录
cd /path/to/astrbot/plugins

# 克隆仓库
git clone https://github.com/Renyus/astrbot_plugin_self_evolution.git
```

✅ **验证安装成功**：重启 AstrBot，日志中出现 `[SelfEvolution] 核心组件初始化完成`

---

## 第二步：创建知识库（2分钟）

知识库是存储长期记忆的地方，**必须创建才能使用记忆功能**。

### 操作步骤

1. 进入 AstrBot 管理后台 →「知识库」
2. 点击「新建知识库」
3. 填写名称：`self_evolution_memory`（默认名称，可在配置中修改）
4. 知识库类型选择「混合检索」
5. 点击「创建」

```
📌 注意：
- 如果改了配置中的 memory_kb_name，这里要用改后的名称
- 每个群聊/私聊会自动创建独立子知识库，无需手动操作
```

✅ **验证知识库创建成功**：在知识库列表能看到刚创建的知识库

---

## 第三步：基础配置（1分钟）

进入 AstrBot 后台 →「插件配置」→ 找到 `astrbot_plugin_self_evolution`

### 必改配置

| 配置项 | 默认值 | 建议修改 |
|--------|--------|----------|
| `persona_name` | 黑塔 | 改成你的机器人名字 |
| `memory_kb_name` | self_evolution_memory | 如果第二步改了名称，这里要一致 |

### 建议开启的功能

| 功能 | 配置项 | 作用 |
|------|--------|------|
| 长期记忆 | `memory_enabled` | 记录每日总结和用户特征 |
| 用户画像 | `enable_profile_injection` | 让机器人"认识"用户 |
| 主动参与 | `interject_enabled` | 机器人会主动插话 |
| SAN系统 | `san_enabled` | 精力系统，更生动 |
| 情感积分 | `affinity_auto_enabled` | 自动增减好感度 |

✅ **验证配置生效**：保存后重载插件，无报错即成功

---

## 第四步：验证功能（5分钟）

### 测试 1：基础命令

在群里或私聊发送：

```
/system help
/system version
```

✅ 预期：机器人返回帮助信息和版本号

---

### 测试 2：记忆功能

在群里进行一段对话，然后发送：

```
/reflect
```

等待几分钟后，询问：

```
刚刚我们聊了什么？
```

✅ 预期：机器人能概括刚才的对话内容

---

### 测试 3：用户画像

发送：

```
/profile view
```

然后告诉机器人：

```
我喜欢吃火锅，以后记得推荐火锅相关的
```

再过几轮对话后，问：

```
你知道我喜欢吃什么吗？
```

✅ 预期：机器人能回答"火锅"

---

### 测试 4：主动参与

如果开启了 `interject_enabled`，在群里连续聊天（10条以上消息），机器人会在适当时候主动插话。

✅ 预期：机器人主动发送一条消息参与讨论

---

## 第五步：进阶探索

恭喜！基础功能已验证通过。接下来可以探索：

| 进阶功能 | 文档 | 说明 |
|---------|------|------|
| 调整参与积极性 | [配置说明](Configuration) | 修改插嘴频率、冷却时间 |
| 自定义画像更新 | [记忆系统](Memory-System) | 了解画像如何生成 |
| 使用 LLM 工具 | [LLM工具](LLM-Tools) | 让 AI 能主动回想过去 |
| 故障排查 | [故障排查](Troubleshooting) | 解决遇到的问题 |

---

## 快速配置模板

### 新手模式（推荐）

```json
{
  "persona_name": "你的机器人名字",
  "memory_enabled": true,
  "enable_profile_injection": true,
  "interject_enabled": false,
  "san_enabled": true,
  "affinity_auto_enabled": true
}
```

### 活跃模式（机器人更主动）

```json
{
  "persona_name": "你的机器人名字",
  "memory_enabled": true,
  "enable_profile_injection": true,
  "interject_enabled": true,
  "interject_interval": 30,
  "interject_cooldown": 30,
  "san_enabled": true,
  "affinity_auto_enabled": true
}
```

---

## 下一步

- 📖 阅读 [配置说明](Configuration) 了解所有参数
- 🧠 了解 [记忆系统](Memory-System) 工作原理
- ❓ 遇到问题查看 [故障排查](Troubleshooting)
