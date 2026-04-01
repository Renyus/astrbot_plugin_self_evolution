import logging

import aiosqlite

logger = logging.getLogger("astrbot")


class PersonaManager:
    """
     CognitionCore 人格进化管理模块
    负责人格进化请求的审核、批准、拒绝等操作
    """

    def __init__(self, plugin):
        self.plugin = plugin

    @property
    def review_mode(self):
        return self.plugin.cfg.review_mode

    @property
    def admin_users(self):
        return self.plugin.cfg.admin_users

    async def evolve_persona(self, event, new_system_prompt: str, reason: str) -> str:
        """提出进化建议"""
        logger.info(f"[Persona] 收到进化请求，reason: {reason[:50]}")
        curr_persona_id = getattr(event, "persona_id", None)
        if not curr_persona_id:
            try:
                conv_mgr = self.plugin.context.conversation_manager
                umo = event.unified_msg_origin
                cid = await conv_mgr.get_curr_conversation_id(umo)
                conversation = await conv_mgr.get_conversation(umo, cid) if cid else None
                conversation_persona_id = conversation.persona_id if conversation else None

                cfg = self.plugin.context.get_config(umo=umo).get("provider_settings", {})

                (
                    curr_persona_id,
                    _,
                    _,
                    _,
                ) = await self.plugin.context.persona_manager.resolve_selected_persona(
                    umo=umo,
                    conversation_persona_id=conversation_persona_id,
                    platform_name=event.get_platform_name(),
                    provider_settings=cfg,
                )
            except Exception as e:
                logger.error(f"[SelfEvolution] 使用 resolve_selected_persona 获取人格 ID 失败: {e}")
                curr_persona_id = "default"

        if not curr_persona_id or curr_persona_id == "default":
            logger.debug(f"[SelfEvolution] 进化被拒绝：当前人格 ID 为 {curr_persona_id}，无法进化默认人格。")
            return "当前未设置自定义人格 (Persona)，无法进行进化。请先在 AstrBot 后台创建并激活一个人格。"

        if self.review_mode:
            try:
                await self.plugin.dao.add_pending_evolution(curr_persona_id, new_system_prompt, reason)
                logger.warning(f"[SelfEvolution] EVOLVE_QUEUED: 收到进化请求，已加入审核队列。原因: {reason}")
                return f"进化请求已录入系统审核队列，等待管理员确认。进化理由：{reason}"
            except aiosqlite.Error as e:
                logger.error(f"[SelfEvolution] EVOLVE_FAILED: 写入审核队列时发生异步数据库异常: {e}")
                return "写入审核队列时发生持久化存储异常，请告知管理员。"

        try:
            await self.plugin.context.persona_manager.update_persona(
                persona_id=curr_persona_id, system_prompt=new_system_prompt
            )
            logger.info(f"[SelfEvolution] EVOLVE_APPLIED: 人格进化成功！Persona: {curr_persona_id}, 原因: {reason}")
            return f"进化成功！我已经更新了我的核心预设。进化理由：{reason}"
        except Exception as e:
            logger.error(f"[SelfEvolution] EVOLVE_FAILED: 进化失败: {e!s}")
            return "进化过程中出现内部错误，请通知管理员检查日志。"

    async def review_evolutions(self, event, page: int = 1) -> str:
        """列出待审核请求"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            return "权限拒绝：此操作仅限系统管理员执行。已记录越权尝试。"

        try:
            limit = 10
            offset = (max(1, page) - 1) * limit
            rows = await self.plugin.dao.get_pending_evolutions(limit, offset)

            if not rows:
                if page == 1:
                    return "当前没有待审核的进化请求。"
                else:
                    return f"第 {page} 页尚未发现待审核的进化请求。"

            result = [f"待审核的进化请求列表 (第 {page} 页):"]
            for row in rows:
                result.append(f"ID: {row['id']} | Persona: {row['persona_id']}\n理由: {row['reason'][:200]}")

            result.append(
                "\n如需批准，请调用 '/evolution approve <ID>'。如需翻看下一页，请调用 '/evolution review <页码>'"
            )
            return "\n".join(result)
        except aiosqlite.Error as e:
            logger.error(f"[SelfEvolution] 获取审核列表失败 (DB Error): {e}")
            return "获取审核列表失败，数据库发生异常，请查看日志。"

    async def approve_evolution(self, event, request_id: int) -> str:
        """批准进化"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            return "权限拒绝：此操作仅限系统管理员执行。已记录越权尝试。"

        try:
            row = await self.plugin.dao.get_evolution(request_id)

            if not row:
                return f"找不到待处理的请求 ID {request_id}。"

            await self.plugin.context.persona_manager.update_persona(
                persona_id=row["persona_id"], system_prompt=row["new_prompt"]
            )

            try:
                await self.plugin.dao.update_evolution_status(request_id, "approved")
                logger.info(f"[SelfEvolution] 管理员批准了进化请求 ID: {request_id}")
                return f"成功批准了进化请求 {request_id}，大模型人格已更新！"
            except Exception as e:
                logger.error(f"[SelfEvolution] 致命异常：大模型人格已更新成功，但在同步数据库状态时多次重试均失败: {e}")
                return f"⚠️ 警告：大模型核心人格已经成功进化！但由于数据库操作中断，审批状态列表（ID {request_id}）未能正确刷新为已批准。底层接口具备幂等性，请管理员排查环境后稍后尝试重复操作以补齐状态。"

        except aiosqlite.Error as e:
            logger.error(f"[SelfEvolution] 读取/状态更新发生数据库操作阻断: {e}")
            return "处理请求期间出现底层数据库异常，请查阅日志。"
        except Exception as e:
            if isinstance(e, (TypeError, ValueError)):
                raise
            logger.error(f"[SelfEvolution] 批准进化请求发生泛用(外部/业务)异常: {e}")
            return f"执行审批与人格变更时遭遇异常({e.__class__.__name__})，请查阅日志。"

    async def reject_evolution(self, event, request_id: int) -> str:
        """拒绝进化"""
        if not event.is_admin() and (not self.admin_users or str(event.get_sender_id()) not in self.admin_users):
            return "权限拒绝：此操作仅限系统管理员执行。"

        try:
            await self.plugin.dao.update_evolution_status(request_id, "rejected")
            logger.info(f"[SelfEvolution] 管理员拒绝了进化请求 ID: {request_id}")
            return f"已成功拒绝并清理进化请求 {request_id}。"
        except Exception as e:
            logger.error(f"[SelfEvolution] 拒绝进化请求失败: {e}")
            return f"拒绝请求时发生异常: {e}"
