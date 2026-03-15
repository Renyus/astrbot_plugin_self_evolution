"""
Scheduler Register - 定时任务注册
"""

import logging

logger = logging.getLogger("astrbot")

from .tasks import (
    scheduled_interject,
    scheduled_memory_summary,
    scheduled_profile_cleanup,
    scheduled_reflection,
    scheduled_san_analyze,
    scheduled_sticker_tag,
)


async def register_tasks(plugin):
    """注册所有定时任务"""
    logger.info("[SelfEvolution] 开始注册定时任务...")

    try:
        cron_mgr = plugin.context.cron_manager

        # 清理旧任务
        try:
            jobs = await cron_mgr.list_jobs()
            for job in jobs:
                if job.name.startswith("SelfEvolution_"):
                    try:
                        await cron_mgr.delete_job(job.job_id)
                        logger.info(f"[SelfEvolution] 已清理旧任务: {job.name}")
                    except Exception as e:
                        logger.warning(f"[SelfEvolution] 清理旧任务失败: {job.name}, {e}")
        except Exception as e:
            logger.warning(f"[SelfEvolution] 获取任务列表失败: {e}")

        # 注册画像清理任务（每天凌晨 4 点）
        await cron_mgr.add_basic_job(
            name="SelfEvolution_ProfileCleanup",
            cron_expression="0 4 * * *",
            handler=lambda: scheduled_profile_cleanup(plugin),
            description="自我进化插件：清理过期用户画像。",
            persistent=True,
        )
        logger.info("[SelfEvolution] 已注册画像清理任务: 0 4 * * *")

        # 注册每日自省任务
        await cron_mgr.add_basic_job(
            name="SelfEvolution_DailyReflection",
            cron_expression=plugin.reflection_schedule,
            handler=lambda: scheduled_reflection(plugin),
            description="自我进化插件：每日定时深度自省标记。",
            persistent=True,
        )
        logger.info(f"[SelfEvolution] 已注册定时自省任务: {plugin.reflection_schedule}")

        # 注册表情包打标签任务（每 N 分钟）
        if plugin.cfg.sticker_learning_enabled:
            sticker_tag_interval = plugin.cfg.sticker_tag_cooldown
            sticker_tag_cron = f"*/{sticker_tag_interval} * * * *"
            await cron_mgr.add_basic_job(
                name="SelfEvolution_StickerTag",
                cron_expression=sticker_tag_cron,
                handler=lambda: scheduled_sticker_tag(plugin),
                description="自我进化插件：定时给表情包打标签。",
                persistent=True,
            )
            logger.info(f"[SelfEvolution] 已注册表情包打标签任务: {sticker_tag_cron}")

        # 注册 SAN 分析任务
        if plugin.cfg.san_enabled and plugin.cfg.san_auto_analyze_enabled:
            san_interval = plugin.cfg.san_analyze_interval
            san_cron = f"*/{san_interval} * * * *"
            await cron_mgr.add_basic_job(
                name="SelfEvolution_SANAnalyze",
                cron_expression=san_cron,
                handler=lambda: scheduled_san_analyze(plugin),
                description="自我进化插件：定时分析群状态调整SAN值。",
                persistent=True,
            )
            logger.info(f"[SelfEvolution] 已注册 SAN 分析任务: {san_cron}")

        # 注册每日群聊总结任务
        summary_cron = plugin.cfg.memory_summary_schedule
        await cron_mgr.add_basic_job(
            name="SelfEvolution_MemorySummary",
            cron_expression=summary_cron,
            handler=lambda: scheduled_memory_summary(plugin),
            description="自我进化插件：定时总结群聊消息。",
            persistent=True,
        )
        logger.info(f"[SelfEvolution] 已注册每日总结任务: {summary_cron}")

        # 注册主动插嘴任务
        if plugin.cfg.interject_enabled:
            interject_interval = plugin.cfg.interject_interval
            interject_cron = f"*/{interject_interval} * * * *"
            await cron_mgr.add_basic_job(
                name="SelfEvolution_Interject",
                cron_expression=interject_cron,
                handler=lambda: scheduled_interject(plugin),
                description="自我进化插件：定时检查群聊氛围并自主决定是否插嘴。",
                persistent=True,
            )
            logger.info(f"[SelfEvolution] 已注册主动插嘴任务: {interject_cron}")

        logger.info("[SelfEvolution] 所有定时任务注册完成")

    except Exception as e:
        logger.error(f"[SelfEvolution] 注册定时任务失败: {e}", exc_info=True)
