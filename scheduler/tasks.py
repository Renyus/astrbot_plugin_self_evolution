"""
Scheduler Tasks - 定时任务回调实现
"""

import logging

logger = logging.getLogger("astrbot")


async def scheduled_reflection(plugin):
    """定时任务回调函数 - 做梦机制"""
    plugin.daily_reflection_pending = True
    logger.debug("[SelfEvolution] 每日反思定时任务已触发，将在下一次对话时顺带执行深层内省。")

    await plugin.dao.init_db()
    await plugin.dao.recover_all_affinity(recovery_amount=2)
    logger.debug('[SelfEvolution] 已执行每日"大赦天下"：所有负面评分用户好感度已小幅回升。')


async def scheduled_san_analyze(plugin):
    """SAN 分析定时任务 - 分析群状态动态调整 SAN 值，支持热更新"""
    logger.debug("[SAN] 开始定时分析群状态...")

    san_interval = plugin.cfg.san_analyze_interval
    new_cron = f"*/{san_interval} * * * *"
    try:
        cron_mgr = plugin.context.cron_manager
        jobs = await cron_mgr.list_jobs()
        for job in jobs:
            if job.name == "SelfEvolution_SANAnalyze":
                if job.cron_expression != new_cron:
                    await cron_mgr.update_job(job.job_id, cron_expression=new_cron)
                    logger.debug(f"[SAN] 热更新 cron 表达式: {job.cron_expression} -> {new_cron}")
                break
    except Exception as e:
        logger.warning(f"[SAN] 热更新检查失败: {e}")

    await plugin.san_system.analyze_all_groups()
    logger.debug("[SAN] 定时分析完成。")


async def scheduled_memory_summary(plugin):
    """每日群聊总结任务"""
    logger.debug("[Memory] 开始每日群聊总结...")
    await plugin.memory.daily_summary()
    logger.debug("[Memory] 每日群聊总结任务完成。")


async def scheduled_interject(plugin):
    """主动插嘴定时任务 - 获取群消息，LLM判断是否需要插嘴"""
    logger.debug("[Interject] 开始主动插嘴检查...")

    try:
        # 方式1: 白名单配置
        whitelist = plugin.cfg.interject_whitelist
        if whitelist:
            logger.debug(f"[Interject] 使用白名单群列表: {whitelist}")
            groups = whitelist
        # 方式2: eavesdropping active_users
        elif plugin.eavesdropping.active_users:
            groups = list(plugin.eavesdropping.active_users.keys())
            logger.debug(f"[Interject] 使用 eavesdropping 活跃群列表: {groups}")
        # 方式3: 通过 platform 获取 bot 加入的群列表
        else:
            groups = await _fetch_groups_from_platform(plugin)
            if not groups:
                logger.debug("[Interject] 无目标群")
                return

        logger.debug(f"[Interject] 目标群列表: {groups}")

        for group_id in groups:
            await plugin.eavesdropping.interject_check_group(group_id)

        logger.debug("[Interject] 主动插嘴检查完成")
    except Exception as e:
        logger.warning(f"[Interject] 定时任务执行失败: {e}")


async def _fetch_groups_from_platform(plugin):
    """从 platform 获取 bot 加入的群列表"""
    try:
        platform = plugin.context.platform_manager.platform_insts[0]
        bot = platform.get_client()
        result = await bot.call_action("get_group_list")
        if isinstance(result, list):
            groups_data = result
        elif isinstance(result, dict):
            groups_data = result.get("data", [])
        else:
            groups_data = []
        groups = [str(g.get("group_id", "")) for g in groups_data if g.get("group_id")]
        if groups:
            logger.debug(f"[Interject] 获取到 bot 加入的群列表: {groups}")
        return groups
    except Exception as e:
        logger.debug(f"[Interject] 获取群列表失败: {e}")
        return []


async def scheduled_sticker_tag(plugin):
    """定时给表情包打标签"""
    logger.debug("[Sticker] 开始定时打标签...")

    try:
        await plugin.entertainment.tag_stickers()
        logger.debug("[Sticker] 定时打标签完成")
    except Exception as e:
        logger.warning(f"[Sticker] 定时打标签失败: {e}")


async def scheduled_profile_cleanup(plugin):
    """清理过期用户画像"""
    logger.debug("[Profile] 开始清理过期画像...")

    try:
        await plugin.profile.cleanup_expired_profiles()
        logger.debug("[Profile] 清理过期画像完成")
    except Exception as e:
        logger.warning(f"[Profile] 清理过期画像失败: {e}")


async def scheduled_profile_build(plugin):
    """定时批量构建用户画像"""
    logger.debug("[Profile] 开始定时批量构建画像...")

    if not plugin.cfg.auto_profile_enabled:
        logger.debug("[Profile] 自动画像构建已关闭")
        return

    try:
        whitelist = plugin.cfg.profile_group_whitelist
        if not whitelist:
            logger.debug("[Profile] 白名单为空，使用 bot 加入的群列表")
            groups = await _fetch_groups_from_platform(plugin)
        else:
            groups = whitelist
            logger.debug(f"[Profile] 使用白名单群列表: {groups}")

        if not groups:
            logger.debug("[Profile] 无目标群")
            return

        batch_size = plugin.cfg.auto_profile_batch_size
        batch_interval = plugin.cfg.auto_profile_batch_interval

        logger.debug(f"[Profile] 共有 {len(groups)} 个群，每次处理 {batch_size} 个，间隔 {batch_interval} 分钟")

        import asyncio

        for i in range(0, len(groups), batch_size):
            batch = groups[i : i + batch_size]
            logger.debug(f"[Profile] 处理批次 {i // batch_size + 1}，群: {batch}")

            for group_id in batch:
                try:
                    await plugin.profile.analyze_and_build_profiles(str(group_id))
                except Exception as e:
                    logger.warning(f"[Profile] 群 {group_id} 画像构建失败: {e}")

            if i + batch_size < len(groups):
                logger.debug(f"[Profile] 批次处理完成，等待 {batch_interval} 分钟...")
                await asyncio.sleep(batch_interval * 60)

        logger.debug("[Profile] 定时批量构建画像完成")
    except Exception as e:
        logger.warning(f"[Profile] 定时批量构建画像失败: {e}")
