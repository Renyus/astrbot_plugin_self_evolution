"""
Scheduler Tasks - 定时任务回调实现
"""

import logging

logger = logging.getLogger("astrbot")


async def scheduled_reflection(plugin):
    """定时任务回调函数 - 做梦机制"""
    plugin.daily_reflection_pending = True
    logger.info(
        "[SelfEvolution] 每日反思定时任务已触发，将在下一次对话时顺带执行深层内省。"
    )

    await plugin.dao.init_db()
    await plugin.dao.recover_all_affinity(recovery_amount=2)
    logger.info(
        '[SelfEvolution] 已执行每日"大赦天下"：所有负面评分用户好感度已小幅回升。'
    )


async def scheduled_san_analyze(plugin):
    """SAN 分析定时任务 - 分析群状态动态调整 SAN 值，支持热更新"""
    logger.info("[SAN] 开始定时分析群状态...")

    san_interval = plugin.cfg.san_analyze_interval
    new_cron = f"*/{san_interval} * * * *"
    try:
        cron_mgr = plugin.context.cron_manager
        jobs = await cron_mgr.list_jobs()
        for job in jobs:
            if job.name == "SelfEvolution_SANAnalyze":
                if job.cron_expression != new_cron:
                    await cron_mgr.update_job(job.job_id, cron_expression=new_cron)
                    logger.info(
                        f"[SAN] 热更新 cron 表达式: {job.cron_expression} -> {new_cron}"
                    )
                break
    except Exception as e:
        logger.warning(f"[SAN] 热更新检查失败: {e}")

    await plugin.san_system.analyze_all_groups()
    logger.info("[SAN] 定时分析完成。")


async def scheduled_memory_summary(plugin):
    """每日群聊总结任务"""
    logger.info("[Memory] 开始每日群聊总结...")
    await plugin.memory.daily_summary()
    logger.info("[Memory] 每日群聊总结任务完成。")


async def scheduled_interject(plugin):
    """主动插嘴定时任务 - 获取群消息，LLM判断是否需要插嘴"""
    logger.info("[Interject] 开始主动插嘴检查...")

    try:
        groups = list(plugin.eavesdropping.active_users.keys())
        if not groups:
            logger.debug("[Interject] 无目标群（eavesdropping 未监听任何群）")
            return

        logger.info(f"[Interject] 目标群列表: {groups}")

        for group_id in groups:
            await plugin.eavesdropping.interject_check_group(group_id)

        logger.info("[Interject] 主动插嘴检查完成")
    except Exception as e:
        logger.warning(f"[Interject] 定时任务执行失败: {e}")


async def scheduled_sticker_tag(plugin):
    """定时给表情包打标签"""
    logger.info("[Sticker] 开始定时打标签...")

    try:
        await plugin.entertainment.auto_tag_stickers()
        logger.info("[Sticker] 定时打标签完成")
    except Exception as e:
        logger.warning(f"[Sticker] 定时打标签失败: {e}")


async def scheduled_profile_cleanup(plugin):
    """清理过期用户画像"""
    logger.info("[Profile] 开始清理过期画像...")

    try:
        await plugin.profile.cleanup_expired_profiles()
        logger.info("[Profile] 清理过期画像完成")
    except Exception as e:
        logger.warning(f"[Profile] 清理过期画像失败: {e}")
