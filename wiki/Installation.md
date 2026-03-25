# 安装指南

> 详细的安装步骤和前置检查，确保一次安装成功

---

## 安装前检查清单

在开始前，请确认以下事项：

- [ ] 已安装 AstrBot 并正常运行
- [ ] 已配置可用的 LLM 模型（如 Deepseek、Hunyuan、Qwen等）
- [ ] 使用 NapCat 作为消息协议后端（QQ）
- [ ] 有管理员权限（可操作 AstrBot 后台）

⚠️ **重要提示**：
- 本插件仅支持 NapCat 作为消息后端
- LLM 模型必须可用，否则记忆和画像功能无法工作

---

## 安装步骤

### Step 1: 进入插件市场

1. 打开 AstrBot 管理后台（通常是 `http://localhost:6185`）
2. 点击左侧菜单「插件」→「插件市场」
3. 在搜索框输入 `self_evolution`

---

### Step 2: 安装插件

1. 找到 `astrbot_plugin_self_evolution`
2. 点击「安装」按钮
3. 等待安装完成（约10-30秒）

---

### Step 3: 创建知识库（关键步骤）

这是最容易遗漏的步骤！

1. 在 AstrBot 后台点击「知识库」
2. 点击「新建知识库」

```
知识库名称：self_evolution_memory
知识库类型：混合检索
```

3. 其他保持默认，点击「创建」

📌 **为什么必须创建知识库？**
- 长期记忆存储在这里
- 群聊总结、用户事件都保存在知识库
- 如果不创建，记忆功能将无法工作

---

### Step 4: 重载插件

1. 进入「插件」→「已安装插件」
2. 找到 `astrbot_plugin_self_evolution`
3. 点击「重载」或重启整个 AstrBot

---

## 安装验证

### 验证 1：日志检查

查看 AstrBot 日志，寻找以下输出：

```
[Core] [INFO]：正在载入插件 astrbot_plugin_self_evolution ...
[Core] [INFO]：Plugin astrbot_plugin_self_evolution (Ver 3.2.0) by renyus: CognitionCore 7.0 数字生命
[Core] [INFO]：[SelfEvolution] 核心组件 (DAO, Eavesdropping, Entertainment, ImageCache, MetaInfra, Memory, Persona, Profile, SAN, Reflection, SessionMemory*, Profile*) 初始化完成。
[Core] [INFO]：[SelfEvolution] DAO: 成功在长连接池状态机的保护下建立/验证数据库。
```

### 验证 2：命令测试

在任意群聊或私聊发送：

```
/system help
/system version
```

✅ **成功标志**：机器人返回帮助信息和版本号

### 验证 3：配置检查

进入 AstrBot 后台 →「插件配置」，确认能看到以下配置分组：

- base（基础配置）
- memory_summary（记忆与总结）
- profile（用户画像）
- engagement（社交参与）
- affinity（情感积分）
- san（SAN系统）

---

## 常见问题预处理

### Q: 安装后插件不生效？

**排查步骤：**

1. 检查日志是否有错误信息
2. 确认知识库已创建（最常见原因）
3. 确认 NapCat 连接正常
4. 尝试重启 AstrBot

---

## 卸载与重装

### 卸载插件

1. AstrBot 后台 →「插件」→「已安装插件」
2. 找到本插件，点击「卸载」

### 重装插件

直接按安装步骤重新安装即可，如果卸载本插件时没有勾选数据删除，重装后数据会保留。

---

## 下一步

- 🚀 [快速入门](Quick-Start) - 5分钟启用核心功能
- ⚙️ [配置说明](Configuration) - 了解所有配置项
- 🧠 [记忆系统](Memory-System) - 理解工作原理
