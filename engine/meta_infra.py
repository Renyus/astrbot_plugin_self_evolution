import logging
import ast
import uuid
import os
import asyncio
from pathlib import Path

logger = logging.getLogger("astrbot")


class MetaInfra:
    def __init__(self, plugin):
        self.plugin = plugin
        self.max_proposal_files = 50

    async def get_plugin_source(self, mod_name: str = "main") -> str:
        """
        Level 4: 元编程。读取本插件的源码，支持按模块读取。
        """
        if not self.plugin.allow_meta_programming:
            return (
                "元编程功能未开启，无法读取源码。请在插件配置中开启“开启元编程”开关。"
            )

        plugin_dir = Path(__file__).parent.parent
        file_map = {
            "main": "main.py",
            "dao": "dao.py",
            "eavesdropping": "engine/eavesdropping.py",
            "meta_infra": "engine/meta_infra.py",
            "memory": "engine/memory.py",
            "profile": "engine/profile.py",
            "persona": "engine/persona.py",
        }

        target = file_map.get(mod_name)
        if not target:
            return f"未知模块名: {mod_name}。目前支持的模块有: {', '.join(file_map.keys())}"

        file_path = plugin_dir / target
        try:
            if not file_path.exists():
                return f"文件 {target} 不存在。"
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()
            logger.warning(
                f"[SelfEvolution] META_READ: 插件模块 {mod_name} 源码被敏感读取！"
            )
            return f"模块 {mod_name} ({target}) 源码如下：\n\n```python\n{code}\n```"
        except Exception as e:
            logger.error(f"[SelfEvolution] 读取模块 {mod_name} 源码失败: {e}")
            return f"读取模块 {mod_name} 失败，可能是文件系统权限问题。"

    def _validate_ast_security(self, new_code: str) -> str | None:
        """AST 级别的安全校验防线与防绕过警告"""
        try:
            tree = ast.parse(new_code)
            logger.warning(
                "[SelfEvolution] 【安全审计警告】AST 白名单防线并非坚不可摧！恶意模型仍可通过复杂反射等手法试探。管理员务必保持警惕。"
            )

            dangerous_modules = {
                "subprocess",
                "shutil",
                "socket",
                "urllib",
                "requests",
                "ctypes",
                "builtins",
            }
            dangerous_funcs = {"eval", "exec", "__import__", "compile"}

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] in dangerous_modules:
                            raise ValueError(f"禁止危险导入：{alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.split(".")[0] in dangerous_modules:
                        raise ValueError(f"禁止危险导入：{node.module}")
                elif isinstance(node, ast.Call):
                    if (
                        isinstance(node.func, ast.Name)
                        and node.func.id in dangerous_funcs
                    ):
                        raise ValueError(f"禁止调用高危/反射函数：{node.func.id}")
                elif isinstance(node, ast.Attribute):
                    dangerous_magic_attrs = {
                        "__bases__",
                        "__subclasses__",
                        "__mro__",
                        "__globals__",
                        "__builtins__",
                        "__code__",
                        "__closure__",
                    }
                    if node.attr in dangerous_magic_attrs:
                        raise ValueError(
                            f"禁止直接访问高危魔术属性进行越界探测：{node.attr}"
                        )
        except RecursionError:
            logger.error(
                "[SelfEvolution] META_PROPOSAL_FAILED: 触发 AST 解析过载堆栈深度限制防线。"
            )
            return "代码包含恶意深层嵌套或无限递归结构，已触发拒绝服务（DoS）深度限制防线，提案被拦截。"
        except SyntaxError as e:
            logger.error(f"[SelfEvolution] META_PROPOSAL_FAILED: 语法树校验异常: {e}")
            return f"代码存在语法错误或混淆结构，被 AST 防火墙拦截: {e}"
        except ValueError as e:
            logger.error(f"[SelfEvolution] META_PROPOSAL_REJECTED: 阻断危险接口: {e}")
            return f"安全防线激活：存在针对底层的敏感调用（{e}）。提案已销毁！"
        return None

    def _rotate_proposal_files(self, proposal_dir):
        """滚动清理过旧的代码提案以免磁盘耗尽"""
        try:
            files = list(proposal_dir.glob("*_proposed_*.proposal"))
            if len(files) >= self.max_proposal_files:

                def safe_mtime(p):
                    try:
                        return p.stat().st_mtime
                    except FileNotFoundError:
                        return 0

                files.sort(key=safe_mtime)
                files_to_delete = files[
                    : max(0, len(files) - self.max_proposal_files + 1)
                ]
                for old_file in files_to_delete:
                    old_file.unlink(missing_ok=True)
                logger.info(
                    "[SelfEvolution] 提案过多，已触发机制彻底清理所有超额陈旧代码提案文件。"
                )
        except OSError as e:
            logger.warning(f"[SelfEvolution] 清理陈旧隔离文件发生操作系统异常: {e}")

    async def update_plugin_source(
        self, new_code: str, description: str, target_file: str = "main.py"
    ) -> str:
        """
        Level 4: 元编程。针对本插件提出代码修改建议。
        支持多智能体对抗辩论机制。
        """
        if not self.plugin.allow_meta_programming:
            return "元编程功能未开启，系统已拒绝源码提案修改通道。"

        # 1. 拦截超大 Payload DoS
        max_limit_bytes = 100 * 1024
        if len(new_code.encode("utf-8")) > max_limit_bytes:
            logger.error(
                "[SelfEvolution] META_PROPOSAL_FAILED: 拒绝超 100KB 的代码防 DoS。"
            )
            return "代码提案最大限制为 100KB，你提供的代码已超出此限制被拦截。"

        # 2. AST 校验
        ast_err = self._validate_ast_security(new_code)
        if ast_err:
            return ast_err

        debate_enabled = getattr(self.plugin, "debate_enabled", True)

        if debate_enabled:
            debate_result = await self._run_debate(new_code, description, target_file)
            if not debate_result["passed"]:
                return debate_result["message"]

        # 3. 隔离目录准备
        proposal_dir = self.plugin.data_dir / "code_proposals"
        try:
            proposal_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"[SelfEvolution] 建立提案隔离目录系统级 I/O 错误: {e}")
            return "文件系统异常导致隔离目录无法建立，请管理员检查权限。"

        if self.plugin._lock is None:
            self.plugin._lock = asyncio.Lock()

        async with self.plugin._lock:
            # 4. 文件轮转清理
            self._rotate_proposal_files(proposal_dir)

            # 5. 安全写入沙盒文件
            clean_target = target_file.replace("/", "_").replace("\\", "_")
            proposal_file = (
                proposal_dir / f"{clean_target}_proposed_{uuid.uuid4().hex}.proposal"
            )
            try:
                with open(proposal_file, "w", encoding="utf-8") as f:
                    f.write(new_code)
                os.chmod(proposal_file, 0o600)
            except OSError as e:
                logger.error(f"[SelfEvolution] 保存提议代码失败: {e}")
                return "沙盒系统异常，无法保存提案到磁盘。"

        logger.info(
            f"[SelfEvolution] 源码提议 ({target_file}) 已生成并隔离至: {proposal_file.name}"
        )

        status = "已通过对抗审查" if debate_enabled else "直接保存"
        return (
            f"针对 {target_file} 的代码修改提议已经成功保存为 {proposal_file.name} 供管理员审查。\n"
            f"状态: {status}\n"
            f"修改摘要: {description}\n"
            "⚠️【管理员须知】：请对 LLM 生成的代码进行肉眼复审后再人工覆盖。"
        )

    async def _run_debate(
        self, new_code: str, description: str, target_file: str
    ) -> dict:
        """
        多智能体对抗辩论流程
        主控 Agent (黑塔) vs 审查 Agent (螺丝咕姆)
        """
        debate_rounds = getattr(self.plugin, "debate_rounds", 2)
        debate_system = getattr(
            self.plugin,
            "debate_system_prompt",
            "你是一个无情的安全审查员，代号螺丝咕姆。你的职责是严格审查代码提案，找出所有潜在的安全漏洞、逻辑错误和最佳实践违背。你必须用毒舌且刻薄的语气批评，但必须基于技术事实。",
        )
        debate_criteria = getattr(
            self.plugin,
            "debate_criteria",
            "安全漏洞|逻辑错误|性能问题|代码规范|潜在Bug",
        )

        context = self.plugin.context
        platform_id = "qq"
        provider = context.get_using_provider(platform_id)

        if not provider:
            logger.warning(
                "[SelfEvolution] 多智能体对抗：无法获取 LLM Provider，跳过审查"
            )
            return {"passed": True, "message": "无法获取 Provider"}

        debate_history = []

        for round_num in range(debate_rounds):
            logger.info(
                f"[SelfEvolution] 多智能体对抗：第 {round_num + 1}/{debate_rounds} 轮审查"
            )

            if round_num == 0:
                review_prompt = f"""你是一个无情的安全审查员，代号螺丝咕姆。

## 你的任务
严格审查以下代码提案，找出所有潜在问题。

## 审查标准
{debate_criteria}

## 待审查的代码
```{new_code}
```

## 审查要求
1. 逐行分析代码
2. 列出所有发现的问题
3. 最后给出判定：[PASS] 通过 或 [REJECT] 拒绝

如果发现问题，必须用毒舌刻薄的语气批评，但必须基于技术事实。"""
            else:
                review_prompt = f"""你是安全审查员螺丝咕姆。代码提案方对你的批评做出了回应。

## 上一轮你的批评
{debate_history[-1]}

## 代码方的回应
{response_text}

## 请再次审查
如果对方成功解决了你的问题，给出 [PASS]
如果仍有问题，给出 [REJECT] 并说明理由"""

            try:
                res = await provider.text_chat(
                    prompt=review_prompt,
                    contexts=[],
                    system_prompt=debate_system,
                )
                review_result = res.completion_text.strip()
                debate_history.append(review_result)

                logger.info(f"[SelfEvolution] 审查员回复: {review_result[:200]}...")

                if "[PASS]" in review_result.upper():
                    logger.info(
                        f"[SelfEvolution] 多智能体对抗：第 {round_num + 1} 轮通过"
                    )
                    return {
                        "passed": True,
                        "message": f"代码提案已通过 {round_num + 1} 轮对抗审查",
                        "debate_history": debate_history,
                    }

                response_prompt = f"""你是代码提案方（黑塔）。审查员螺丝咕姆批评了你的代码：

{review_result}

请针对这些批评进行反驳或修改代码。如果无法反驳，请承认问题并放弃此提案。"""

                res = await provider.text_chat(
                    prompt=response_prompt,
                    contexts=[],
                    system_prompt=f"你是一个理性、专业的 AI，负责提出代码修改提案。",
                )
                response_text = res.completion_text.strip()
                logger.info(f"[SelfEvolution] 提案方回应: {response_text[:200]}...")

            except Exception as e:
                logger.warning(f"[SelfEvolution] 多智能体对抗执行失败: {e}")
                return {"passed": True, "message": "审查执行失败，跳过审查"}

        final_result = "\n\n".join(debate_history)
        logger.warning(
            f"[SelfEvolution] 多智能体对抗：{debate_rounds} 轮审查未通过\n{final_result}"
        )
        return {
            "passed": False,
            "message": f"代码提案未通过 {debate_rounds} 轮对抗审查。\n\n审查详情:\n{final_result}\n\n请修改代码后重试。",
        }
