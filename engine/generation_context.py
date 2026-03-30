from dataclasses import dataclass, field
from typing import Optional

from .speech_types import GenerationSpec, OutputResult, SpeechDecision
from .context_injection import get_group_history


@dataclass
class GenerationContext:
    persona_prompt: str = ""
    identity_block: str = ""
    history_block: str = ""
    profile_block: str = ""
    memory_block: str = ""
    reflection_block: str = ""
    behavior_block: str = ""
    decision_block: str = ""
    anchor_block: str = ""


class ContextBuilder:
    """统一上下文装配器。

    无论主动还是被动，只要生成文本，都走这个 Builder。
    区别只体现在 decision_block 和 anchor_block。
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.cfg = plugin.cfg

    async def build(
        self,
        ctx,
        decision: SpeechDecision,
        anchor_text: str = "",
        scene: str = "",
    ) -> GenerationContext:
        """构建完整的 GenerationContext.

        Args:
            ctx: PromptContext (user_id, group_id, sender_name etc.)
            decision: SpeechDecision (delivery_mode, text_mode, max_chars etc.)
            anchor_text: 锚点文本 (anchor_block)
            scene: 场景类型 (decision_block)
        """
        gc = GenerationContext()

        gc.persona_prompt = await self._get_persona_prompt(ctx)
        gc.identity_block = self._build_identity(ctx)
        gc.history_block = await self._build_history(ctx)
        gc.profile_block = await self._build_profile(ctx)
        gc.memory_block = await self._build_memory(ctx)
        gc.behavior_block = await self._build_behavior(ctx, decision)
        gc.decision_block = self._build_decision_block(decision, scene)
        gc.anchor_block = self._build_anchor_block(anchor_text, decision)

        return gc

    def build_generation_spec(
        self,
        gc: GenerationContext,
        decision: SpeechDecision,
    ) -> GenerationSpec:
        """从 GenerationContext 构建 GenerationSpec"""
        system_parts = [
            gc.persona_prompt,
            gc.identity_block,
            gc.history_block,
            gc.profile_block,
            gc.memory_block,
            gc.behavior_block,
            gc.decision_block,
            gc.anchor_block,
        ]
        system_prompt = "\n\n".join(p for p in system_parts if p.strip())

        user_prompt = self._build_user_prompt(decision)

        return GenerationSpec(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            mode=decision.text_mode if decision.delivery_mode == "text" else decision.delivery_mode,
            max_chars=decision.max_chars,
            strict_output_rules=True,
            decision=decision,
        )

    async def _get_persona_prompt(self, ctx) -> str:
        if hasattr(self.plugin, "_get_active_persona_prompt"):
            umo = getattr(ctx, "umo", None) or (
                getattr(self.plugin, "get_group_umo", lambda g: None)(getattr(ctx, "group_id", ""))
                if hasattr(self.plugin, "get_group_umo")
                else None
            )
            if umo:
                return await self.plugin._get_active_persona_prompt(umo)
        return getattr(self.plugin, "persona_name", "黑塔")

    def _build_identity(self, ctx) -> str:
        parts = [
            f"- 发送者ID: {ctx.user_id}",
            f"- 发送者昵称: {ctx.sender_name}{getattr(ctx, 'role_info', '')}",
            f"- 情感积分: {getattr(ctx, 'affinity', 50)}/100",
        ]
        if getattr(ctx, "is_group", False):
            ctx_parts = []
            if getattr(ctx, "quoted_info", ""):
                ctx_parts.append(ctx.quoted_info)
            if getattr(ctx, "at_info", ""):
                ctx_parts.append(ctx.at_info)
            parts.append("- 来源：群聊")
            if ctx_parts:
                parts.append(f"- 交互上下文: {' + '.join(ctx_parts)}")
        else:
            parts.append("- 来源：私聊")
        if getattr(ctx, "ai_context_info", ""):
            parts.append(ctx.ai_context_info)
        return "\n\n【内部参考信息 - 不要输出】\n" + "\n".join(parts) + "\n"

    async def _build_history(self, ctx) -> str:
        if not getattr(self.cfg, "inject_group_history", False):
            return ""
        group_id = getattr(ctx, "group_id", "")
        if not group_id:
            return ""
        hist = await get_group_history(self.plugin, group_id, self.cfg.group_history_count)
        if hist:
            return f"\n\n【群消息历史】\n{hist}\n"
        return ""

    async def _build_profile(self, ctx) -> str:
        if not getattr(self.plugin, "enable_profile_injection", False):
            return ""
        has_reply = getattr(ctx, "has_reply", False)
        has_at = getattr(ctx, "has_at", False)
        is_group = getattr(ctx, "is_group", False)
        if not (((has_reply or has_at) and is_group) or not is_group):
            return ""
        if hasattr(self.plugin, "_build_profile_injection"):
            return await self.plugin._build_profile_injection(ctx)
        return ""

    async def _build_memory(self, ctx) -> str:
        if not getattr(self.plugin, "enable_kb_memory_recall", False):
            return ""
        if hasattr(self.plugin, "_build_kb_memory_injection"):
            return await self.plugin._build_kb_memory_injection(ctx)
        return ""

    async def _build_behavior(self, ctx, decision: SpeechDecision) -> str:
        parts = []

        if decision.delivery_mode == "text" and decision.text_mode == "interject":
            parts.append(
                "[主动发言模式]\n"
                "你是主动加入群聊讨论的。请基于上下文自然接话，不要开启新话题，"
                "不要过度打断，保持简短（50字以内），只输出回复正文。"
            )

        if hasattr(self.plugin, "_should_inject_preference_hints") and self.plugin._should_inject_preference_hints(ctx):
            parts.append(
                "[即时画像更新提示]\n"
                "用户在表达偏好或身份信息变化，请主动调用 upsert_cognitive_memory 工具更新该用户的印象笔记，"
                "确保当天的记忆准确无误。"
            )

        if hasattr(self.plugin, "san_enabled") and self.plugin.san_enabled:
            san_injection = self.plugin.san_system.get_prompt_injection()
            if san_injection:
                parts.append(san_injection)

        if hasattr(self.plugin, "entertainment") and getattr(self.cfg, "sticker_learning_enabled", False):
            get_sticker_inj = getattr(self.plugin.entertainment, "get_prompt_injection", None)
            if get_sticker_inj:
                sticker_injection = await get_sticker_inj()
                if sticker_injection:
                    parts.append(sticker_injection)

        reply_format = self._get_reply_format()
        if reply_format:
            parts.append(reply_format)

        return "\n\n" + "\n\n".join(parts) + "\n" if parts else ""

    def _get_reply_format(self) -> str:
        if hasattr(self.plugin, "_get_reply_format"):
            return self.plugin._get_reply_format()
        return ""

    def _build_decision_block(self, decision: SpeechDecision, scene: str = "") -> str:
        if decision.delivery_mode != "text":
            return ""
        mode = decision.text_mode
        max_chars = decision.max_chars
        allow_new = decision.allow_new_topic
        must_follow = decision.must_follow_thread

        constraints = []
        if scene:
            constraints.append(f"场景：{scene}")
        if decision.reason:
            constraints.append(f"发言原因：{decision.reason}")
        constraints.append(f"模式：{mode}")
        constraints.append(f"最大长度：{max_chars}字")
        if must_follow:
            constraints.append("必须顺着上下文，不能开启新话题")
        if not allow_new:
            constraints.append("不要主动延伸话题")

        return "\n\n[发言约束]\n" + "\n".join(constraints) + "\n"

    def _build_anchor_block(self, anchor_text: str, decision: SpeechDecision) -> str:
        if not anchor_text:
            return ""
        if decision.delivery_mode != "text":
            return ""
        return f"\n\n[锚点上下文]\n最近消息：{anchor_text}\n"

    def _build_user_prompt(self, decision: SpeechDecision) -> str:
        if decision.delivery_mode == "text":
            mode = decision.text_mode
            if mode == "interject":
                return "请基于当前群聊上下文，自然接一句话。只输出回复正文，不加任何前缀。"
            elif mode == "reply":
                return "请基于上下文回复。只输出回复正文，不加任何前缀。"
            elif mode == "correction":
                return "请基于上下文进行纠正或补充。只输出回复正文，不加任何前缀。"
            elif mode == "disengage":
                return "请自然结束或转移话题。只输出回复正文，不加任何前缀。"
            else:
                return "请基于上下文自然回复。只输出回复正文，不加任何前缀。"
        elif decision.delivery_mode == "emoji":
            return "请发表情包回应。"
        return ""
