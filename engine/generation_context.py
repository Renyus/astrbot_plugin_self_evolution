from dataclasses import dataclass, field
from typing import Optional

from .speech_types import GenerationSpec, OutputResult, SpeechDecision, ResponsePosture, TextLiteVariant
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
        pending_trigger_hint: str = "",
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
        gc.decision_block = self._build_decision_block(decision, scene, pending_trigger_hint)
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
        scope_id = getattr(ctx, "scope_id", None) or getattr(ctx, "group_id", None)
        sim_block = ""
        if scope_id and hasattr(self.plugin, "persona_sim") and self.plugin.persona_sim:
            try:
                snapshot = await self.plugin.persona_sim.get_snapshot(str(scope_id))
                if snapshot:
                    from .persona_sim_injection import snapshot_to_prompt

                    sim_block = snapshot_to_prompt(snapshot)
            except Exception:
                pass

        fallback = getattr(self.plugin, "persona_name", "黑塔")
        if hasattr(self.plugin, "_get_active_persona_prompt"):
            umo = getattr(ctx, "umo", None) or (
                getattr(self.plugin, "get_group_umo", lambda g: None)(getattr(ctx, "group_id", ""))
                if hasattr(self.plugin, "get_group_umo")
                else None
            )
            if umo:
                fallback = await self.plugin._get_active_persona_prompt(umo)

        is_group = getattr(ctx, "is_group", False)
        if not is_group:
            fallback = fallback.strip() + "\n\n[场景修正]\n当前是私聊场景，不是群聊。"

        if sim_block:
            existing = sim_block + "\n\n" + fallback
        else:
            existing = fallback

        arc_block = ""
        if hasattr(self.plugin, "persona_arc") and self.plugin.persona_arc:
            try:
                arc_block = await self.plugin.persona_arc.build_prompt(str(scope_id))
            except Exception:
                pass

        if arc_block:
            return arc_block + "\n\n" + existing
        return existing

    def _build_identity(self, ctx) -> str:
        affinity = getattr(ctx, "affinity", 50)
        if affinity >= 80:
            affinity_desc = "很熟"
        elif affinity >= 60:
            affinity_desc = "还行"
        elif affinity >= 30:
            affinity_desc = "一般"
        else:
            affinity_desc = "陌生"

        role_info = getattr(ctx, "role_info", "")
        role_str = f"（{role_info}）" if role_info else ""

        parts = [
            f"用户：{ctx.sender_name}{role_str}",
            f"你们的关系：{affinity_desc}",
        ]
        if getattr(ctx, "is_group", False):
            parts.append("场景：群聊")
            ctx_parts = []
            if getattr(ctx, "quoted_info", ""):
                ctx_parts.append(f"引用了「{ctx.quoted_info}」")
            if getattr(ctx, "at_info", ""):
                ctx_parts.append("有人@了你")
            if ctx_parts:
                parts.append("当前：" + "，".join(ctx_parts))
        else:
            parts.append("场景：私聊")

        if getattr(ctx, "ai_context_info", ""):
            parts.append(ctx.ai_context_info)

        return "\n\n[背景信息]\n" + "\n".join(parts) + "\n"

    async def _build_history(self, ctx) -> str:
        if not getattr(self.cfg, "inject_group_history", False):
            return ""
        group_id = getattr(ctx, "group_id", "")
        if not group_id:
            return ""
        hist = await get_group_history(self.plugin, group_id, self.cfg.group_history_count)
        if hist:
            return f"\n\n[最近群消息]\n{hist}\n"
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
                "[当前场景]\n"
                "你正在主动参与群聊。看到有意思的话题就自然插一句，不用等被问。\n"
                "短一点，像平时跟朋友聊天那样。"
            )

        if hasattr(self.plugin, "_should_inject_preference_hints") and self.plugin._should_inject_preference_hints(ctx):
            parts.append("[记忆]\n用户透露了个人信息可以顺手记下来，调用 upsert_cognitive_memory 即可。")

        if hasattr(self.plugin, "san_enabled") and self.plugin.san_enabled:
            san_injection = self.plugin.san_system.get_prompt_injection()
            if san_injection:
                parts.append(san_injection)

        if hasattr(self.plugin, "entertainment") and getattr(self.cfg, "sticker_learning_enabled", False):
            get_sticker_inj = getattr(self.plugin.entertainment, "get_prompt_injection", None)
            if get_sticker_inj:
                sticker_injection = await get_sticker_inj()
                if sticker_injection:
                    parts.append("[表情包]\n" + sticker_injection)

        # 时间感知注入
        time_hint = self._build_time_awareness()
        if time_hint:
            parts.append(time_hint)

        # 好感度驱动语气注入
        affinity = getattr(ctx, "affinity", 50)
        tone_hint = self._build_affinity_tone(affinity)
        if tone_hint:
            parts.append(tone_hint)

        reply_format = self._get_reply_format()
        if reply_format:
            parts.append(reply_format)

        style_hint = self._build_style_hint(decision)
        if style_hint:
            parts.append(style_hint)

        return "\n\n" + "\n\n".join(parts) + "\n" if parts else ""

    def _build_time_awareness(self) -> str:
        """根据当前时段从配置注入行为提示。"""
        from datetime import datetime

        hour = datetime.now().hour
        if hasattr(self.plugin, "_get_time_profile_hint"):
            return self.plugin._get_time_profile_hint(hour)
        return ""

    def _build_affinity_tone(self, affinity: int) -> str:
        """基于好感度从配置注入语气提示。"""
        if hasattr(self.plugin, "_get_affinity_profile_hint"):
            return self.plugin._get_affinity_profile_hint(affinity)
        return ""

    def _get_reply_format(self) -> str:
        if hasattr(self.plugin, "_get_reply_format"):
            return self.plugin._get_reply_format()
        return ""

    def _build_style_hint(self, decision: SpeechDecision) -> str:
        if decision.delivery_mode != "text":
            return ""

        hints: list[str] = []

        text_lite_variant_hints = {
            "quick_touch": "存在感要低，一句话带过即可，轻一点",
            "quiet_follow": "轻声跟一句，不要抢话头，低调自然",
            "small_probe": "轻微试探，可以稍微问一句，篇幅偏短",
        }
        variant_hint = text_lite_variant_hints.get(decision.text_lite_variant.value, "")
        if variant_hint:
            hints.append(variant_hint)

        if decision.warmth_hint < -0.1:
            hints.append("语气偏冷淡收敛")
        elif decision.warmth_hint > 0.1:
            hints.append("语气偏温暖热情")

        if decision.initiative_hint > 0.15:
            hints.append("可以更主动一些")
        elif decision.initiative_hint < -0.1:
            hints.append("不要太主动")

        if decision.playfulness_hint > 0.15:
            hints.append("表达可以更轻松有趣一些")
        elif decision.playfulness_hint < -0.1:
            hints.append("表达收敛一些")

        posture_hints = {
            ResponsePosture.QUIET_ACK: "简短回应即可，不要过度展开",
            ResponsePosture.QUICK_COMMENT: "极简一句话，轻描淡写带过",
            ResponsePosture.SOFT_CONTINUE: "顺着话题轻轻接一句，篇幅简短",
            ResponsePosture.PLAYFUL_NUDGE: "语气轻松一些，带点俏皮感",
            ResponsePosture.GENTLE_ANSWER: "温和细致地回应，可以稍微展开",
            ResponsePosture.FULL_JOIN: "积极融入对话，表达完整充分",
        }
        if decision.posture != ResponsePosture.NONE and decision.posture in posture_hints:
            hints.append(posture_hints[decision.posture])

        if hints:
            return "[风格提示] " + " ".join(hints)
        return ""

    def _build_decision_block(self, decision: SpeechDecision, scene: str = "", pending_trigger_hint: str = "") -> str:
        if decision.delivery_mode != "text":
            return ""

        lines = []
        if scene:
            lines.append(f"场景：{scene}")
        if decision.reason:
            lines.append(f"起因：{decision.reason}")
        if pending_trigger_hint:
            lines.append(f"背景：{pending_trigger_hint}")

        mode = decision.text_mode
        max_chars = decision.max_chars

        mode_hints = {
            "reply": "在对方说的话后面接一句",
            "interject": "看到感兴趣的就插一句",
            "correction": "补充或纠正一下",
            "disengage": "自然地结束或转移话题",
        }
        lines.append(f"方式：{mode_hints.get(mode, '自然接话')}，{max_chars}字以内")

        must_follow = decision.must_follow_thread
        allow_new = decision.allow_new_topic
        if must_follow:
            lines.append("顺着话题接，不要开新话题")
        elif not allow_new:
            lines.append("不要主动延伸话题")

        return "\n\n[本轮发言]\n" + "\n".join(lines) + "\n"

    def _build_anchor_block(self, anchor_text: str, decision: SpeechDecision) -> str:
        if not anchor_text:
            return ""
        if decision.delivery_mode != "text":
            return ""
        return f"\n\n[相关消息]\n{anchor_text}\n"

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
