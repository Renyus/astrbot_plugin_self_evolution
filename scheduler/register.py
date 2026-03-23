"""Scheduler job registration."""

import logging

from .tasks import (
    scheduled_affinity_recovery,
    scheduled_interject,
    scheduled_memory_summary,
    scheduled_profile_build,
    scheduled_profile_cleanup,
    scheduled_reflection,
    scheduled_san_analyze,
)

logger = logging.getLogger("astrbot")


def _calc_recovery_cron(reflection_cron: str) -> str:
    try:
        parts = reflection_cron.split()
        minute = int(parts[0])
        hour = int(parts[1])
        new_minute = (minute + 5) % 60
        new_hour = (hour + (minute + 5) // 60) % 24
        return f"{new_minute} {new_hour} * * *"
    except Exception:
        return "5 3 * * *"


async def register_tasks(plugin):
    """Register all cron jobs for the plugin."""
    logger.info("[SelfEvolution] 开始注册定时任务")

    try:
        cron_mgr = plugin.context.cron_manager

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
            logger.warning(f"[SelfEvolution] 获取旧任务列表失败: {e}")

        await cron_mgr.add_basic_job(
            name="SelfEvolution_ProfileCleanup",
            cron_expression="0 4 * * *",
            handler=lambda: scheduled_profile_cleanup(plugin),
            description="自我进化插件：清理过期用户画像。",
            persistent=True,
        )
        logger.info("[SelfEvolution] 已注册画像清理任务: 0 4 * * *")

        if plugin.cfg.auto_profile_enabled:
            profile_cron = plugin.cfg.auto_profile_schedule
            await cron_mgr.add_basic_job(
                name="SelfEvolution_ProfileBuild",
                cron_expression=profile_cron,
                handler=lambda: scheduled_profile_build(plugin),
                description="自我进化插件：定时批量构建用户画像。",
                persistent=True,
            )
            logger.info(f"[SelfEvolution] 已注册自动画像构建任务: {profile_cron}")

        if plugin.cfg.reflection_enabled:
            await cron_mgr.add_basic_job(
                name="SelfEvolution_DailyReflection",
                cron_expression=plugin.reflection_schedule,
                handler=lambda: scheduled_reflection(plugin),
                description="自我进化插件：每日批处理、日报和画像刷新。",
                persistent=True,
            )
            logger.info(f"[SelfEvolution] 已注册每日批处理任务: {plugin.reflection_schedule}")

            recovery_cron = _calc_recovery_cron(plugin.reflection_schedule)
            await cron_mgr.add_basic_job(
                name="SelfEvolution_AffinityRecovery",
                cron_expression=recovery_cron,
                handler=lambda: scheduled_affinity_recovery(plugin),
                description="自我进化插件：每日恢复用户好感度。",
                persistent=True,
            )
            logger.info(f"[SelfEvolution] 已注册好感度恢复任务: {recovery_cron}")

        if plugin.cfg.san_enabled and plugin.cfg.san_auto_analyze_enabled:
            san_interval = plugin.cfg.san_analyze_interval
            san_cron = f"*/{san_interval} * * * *"
            await cron_mgr.add_basic_job(
                name="SelfEvolution_SANAnalyze",
                cron_expression=san_cron,
                handler=lambda: scheduled_san_analyze(plugin),
                description="自我进化插件：定时分析群状态并调整 SAN。",
                persistent=True,
            )
            logger.info(f"[SelfEvolution] 已注册 SAN 分析任务: {san_cron}")

        if plugin.cfg.memory_enabled:
            summary_cron = plugin.cfg.memory_summary_schedule
            await cron_mgr.add_basic_job(
                name="SelfEvolution_MemorySummary",
                cron_expression=summary_cron,
                handler=lambda: scheduled_memory_summary(plugin),
                description="自我进化插件：定时总结群聊和私聊消息。",
                persistent=True,
            )
            logger.info(f"[SelfEvolution] 已注册每日总结任务: {summary_cron}")

        if plugin.cfg.interject_enabled:
            interject_interval = plugin.cfg.interject_interval
            interject_cron = f"*/{interject_interval} * * * *"
            await cron_mgr.add_basic_job(
                name="SelfEvolution_Interject",
                cron_expression=interject_cron,
                handler=lambda: scheduled_interject(plugin),
                description="自我进化插件：定时检查群聊氛围并决定是否插嘴。",
                persistent=True,
            )
            logger.info(f"[SelfEvolution] 已注册主动插嘴任务: {interject_cron}")

        logger.info("[SelfEvolution] 定时任务注册完成")

    except Exception as e:
        logger.error(f"[SelfEvolution] 注册定时任务失败: {e}", exc_info=True)
