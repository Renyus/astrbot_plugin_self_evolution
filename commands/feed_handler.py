"""
Feed Handler - 喂食命令处理

职责：
- 注册 /feed [图片] 指令
- 复用现有的媒体目标抽取
- 直接调用识图provider并传入自定义JSON Prompt
- 调用 PersonaSimEngine.eat() 更新状态
- 请求 LLM 根据状态自然生成回复
"""

import json
import re
import uuid
from typing import Optional

from astrbot.api import logger

from .common import CommandContext


FEED_JSON_PROMPT = """分析图片内容，判断是否为食物。

必须严格按以下 JSON 格式返回（不要输出其他内容）：
{"food_name": "菜名/物品名", "is_food": true/false, "category": "normal/dessert/dark_cuisine/non_food", "calories": 0-100的数值, "tastiness": 0-100的数值}

分类说明：
- normal: 普通菜肴（米饭、炒菜、汤类、肉类等）
- dessert: 甜点（蛋糕、冰淇淋、糖果、奶茶等）
- dark_cuisine: 黑暗料理/不可食用（奇怪的组合、不可描述的东西等）
- non_food: 非食物（石头、玩具、昆虫等）

注意事项：
- calories 表示这份食物的分量/热量密度，0=几乎没有热量，100=热量极高
- tastiness 表示看起来的好吃程度，0=看起来很难吃，100=看起来非常美味
- 如果不是食物，calories 和 tastiness 都设为 0
- 只输出JSON，不要有其他解释性文字
"""


def _parse_food_json(text: str) -> Optional[dict]:
    """从 LLM 返回的文本中解析 JSON 食物数据。"""
    text = text.strip()

    json_match = re.search(r"\{[^{}]*\}", text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    return None


async def _get_image_path(target, plugin_context) -> Optional[str]:
    """从 MediaTarget 获取本地图片路径。"""
    try:
        from ..engine.caption_service import _download_and_hash

        cache_key, local_path = await _download_and_hash(target)
        return local_path
    except Exception as e:
        logger.warning(f"[FeedHandler] 下载图片失败: {e}")
    return None


async def _call_vision_provider(plugin_context, local_path: str) -> Optional[str]:
    """直接调用识图provider，返回原始文本。"""
    try:
        cfg = getattr(plugin_context, "_cfg", None)
        if cfg is None:
            try:
                cfg = plugin_context.get_config().get("provider_settings", {})
            except Exception:
                cfg = {}

        prov_id = cfg.get("default_image_caption_provider_id", "")
        if not prov_id:
            logger.warning("[FeedHandler] 未配置识图provider")
            return None

        provider = plugin_context.get_provider_by_id(prov_id)
        if not provider:
            logger.warning(f"[FeedHandler] provider未找到: {prov_id}")
            return None

        resp = await provider.text_chat(
            prompt=FEED_JSON_PROMPT,
            session_id=uuid.uuid4().hex,
            image_urls=[local_path],
            persist=False,
        )
        return resp.completion_text or ""
    except Exception as e:
        logger.warning(f"[FeedHandler] 调用识图provider失败: {e}")
    return None


async def _generate_feed_response(plugin, food_data: dict, snapshot, scope_id: str) -> str:
    """根据喂食结果和当前状态，让 LLM 生成自然回复。"""
    from ..engine.persona_sim_injection import snapshot_to_prompt

    state = snapshot.state
    food_name = food_data["food_name"]
    category = food_data["category"]
    tastiness = food_data["tastiness"]

    effect_hints = []
    for eff in snapshot.active_effects[-3:]:
        if eff.prompt_hint:
            effect_hints.append(eff.prompt_hint)
    effect_str = " | ".join(effect_hints) if effect_hints else "无特殊状态"

    todo_hint = ""
    if snapshot.pending_todos:
        todo_hint = f"\n当前最想做的事：{snapshot.pending_todos[0].title}（{snapshot.pending_todos[0].reason}）"

    persona_prompt = ""
    try:
        umo = plugin.get_scope_umo(scope_id)
        if umo:
            persona_prompt = await plugin._get_active_persona_prompt(umo)
    except Exception:
        pass

    persona_section = f"\n\n【角色设定】\n{persona_prompt}" if persona_prompt else ""

    prompt = f"""有人给你看了一个东西，问你要不要吃它，你看了之后需要以该角色的身份作出反应。{persona_section}

【当前状态】
- 饱腹感: {state.satiety:.0f}/100
- 心情: {state.mood:.0f}/100
- 活力: {state.energy:.0f}/100
- 社交渴望: {state.social_need:.0f}/100

【本次投喂信息】
- 食物: {food_name}
- 分类: {category}
- 美味程度: {tastiness}/100

【当前效果】
{effect_str}
{todo_hint}

要求：
- 严格遵循角色设定中的语气、性格、说话习惯
- 根据饱腹感和心情状态自然反应
- 如果是 dark_cuisine（黑暗料理）或 non_food（非食物），必须表达强烈厌恶/愤怒/想骂人的情绪，这是原则问题绝对不能忍
- 如果是甜点，可以表达额外的愉悦
- 40字以内，第一人称，像正常聊天回复
- 不要输出任何状态数值，只输出对话正文
"""

    try:
        umo = plugin.get_scope_umo(scope_id)
        if not umo:
            return None
        llm_provider = plugin.context.get_using_provider(umo=umo)
        resp = await llm_provider.text_chat(
            prompt=prompt,
            system_prompt="",
            contexts=[],
        )
        text = resp.completion_text.strip() if hasattr(resp, "completion_text") and resp.completion_text else ""
        if text:
            return text
    except Exception as e:
        logger.warning(f"[FeedHandler] LLM生成回复失败: {e}")

    return None


async def handle_feed(event, plugin) -> str:
    """处理 /feed 命令。"""
    ctx = CommandContext.from_event(event, plugin)

    from ..engine.media_extractor import extract_media_targets, MediaKind

    try:
        targets = await extract_media_targets(event)
    except Exception as e:
        logger.warning(f"[FeedHandler] 提取媒体目标失败: {e}")
        return "图片提取失败了...再试一次？"

    image_targets = [t for t in targets if t.kind == MediaKind.IMAGE and t.can_process_now]
    if not image_targets:
        return "请发送一张图片后再使用 /feed 指令～"

    target = image_targets[0]

    local_path = await _get_image_path(target, plugin)
    if not local_path:
        return "图片下载失败...再试一次？"

    raw_text = await _call_vision_provider(plugin.context, local_path)
    if not raw_text:
        return "图片识别失败了...换个图片试试？"

    logger.debug(f"[FeedHandler] 识图返回: {raw_text[:200]}")

    food_data = _parse_food_json(raw_text)
    if not food_data:
        logger.warning(f"[FeedHandler] JSON解析失败, text={raw_text[:100]}")
        return "图片内容解析失败...我认不出这东西是什么～"

    required_keys = {"food_name", "is_food", "category", "calories", "tastiness"}
    if not all(k in food_data for k in required_keys):
        logger.warning(f"[FeedHandler] JSON缺少必要字段: {food_data}")
        return "图片内容解析结果不完整...再试一次？"

    try:
        food_data["calories"] = max(0, min(100, int(food_data["calories"])))
        food_data["tastiness"] = max(0, min(100, int(food_data["tastiness"])))
        food_data["is_food"] = bool(food_data["is_food"])
    except (ValueError, TypeError) as e:
        logger.warning(f"[FeedHandler] 字段类型转换失败: {e}")
        return "图片内容解析结果格式错误...再试一次？"

    scope_id = ctx.scope_id
    user_id = ctx.sender_id

    try:
        snapshot = await plugin.persona_sim.eat(food_data, scope_id, user_id)
    except Exception as e:
        logger.error(f"[FeedHandler] eat() 调用失败: {e}", exc_info=True)
        return f"吃东西的时候出了点问题...{e}"

    llm_response = await _generate_feed_response(plugin, food_data, snapshot, scope_id)
    if llm_response:
        return llm_response

    state = snapshot.state
    return f"吃了{food_data['food_name']}，饱腹感 {state.satiety:.0f}/100，心情 {state.mood:.0f}/100"
