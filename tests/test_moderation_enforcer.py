"""
Unit tests for engine.moderation_enforcer module.
"""

from __future__ import annotations

from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from tests._helpers import (
    cleanup_workspace_temp_dir,
    install_aiosqlite_stub,
    make_workspace_temp_dir,
)

install_aiosqlite_stub()

from dao import SelfEvolutionDAO
from engine.caption_service import CaptionResult
from engine.media_extractor import MediaKind, MediaOrigin
from engine.moderation_classifier import (
    ModerationCategory,
    ModerationResult,
    RiskLevel,
    SuggestedAction,
)
from engine.moderation_enforcer import (
    EnforcementResult,
    enforce_moderation,
)


class MockEvent:
    def __init__(self):
        self.bot = MagicMock()
        self.bot.api = MagicMock()
        self.bot.api.call_action = AsyncMock(return_value={"retcode": 0})


class ModerationEnforcerTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = make_workspace_temp_dir("enforcer")
        self.dao = SelfEvolutionDAO(str(Path(self.temp_dir) / "test.db"))
        await self.dao.init_db()
        self.event = MockEvent()

    async def asyncTearDown(self):
        await self.dao.close()
        cleanup_workspace_temp_dir(self.temp_dir)

    def _make_caption(self, text="test caption"):
        return CaptionResult(
            text=text,
            success=True,
            provider_id="test_provider",
            model_name="test_model",
            resource_key="url",
            kind=MediaKind.IMAGE,
            origin=MediaOrigin.MESSAGE,
        )

    def _make_result(self, action=SuggestedAction.DELETE, category=ModerationCategory.NSFW):
        return ModerationResult(
            category=category,
            confidence=0.85,
            risk_level=RiskLevel.HIGH,
            reasons=["test reason"],
            suggested_action=action,
            classifier="test",
        )

    async def test_enforcement_disabled_means_dry_run(self):
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.DELETE)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch("engine.moderation_enforcer.execute_delete_message", new_callable=AsyncMock) as mock_delete:
            result = await enforce_moderation(
                self.event,
                "940513526",
                "1367309651",
                "12345",
                cap,
                nsfw,
                promo,
                merged,
                enforcement_enabled=False,
                dao=self.dao,
            )

        self.assertTrue(result.dry_run)
        self.assertEqual(result.final_action, "dryrun_delete")
        mock_delete.assert_not_called()

    async def test_enforcement_enabled_executes_delete(self):
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.DELETE)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch(
            "engine.moderation_enforcer.execute_delete_message", new_callable=AsyncMock, return_value=True
        ) as mock_delete:
            result = await enforce_moderation(
                self.event,
                "940513526",
                "1367309651",
                "12345",
                cap,
                nsfw,
                promo,
                merged,
                enforcement_enabled=True,
                dao=self.dao,
            )

        self.assertFalse(result.dry_run)
        self.assertTrue(result.delete_attempted)
        self.assertTrue(result.delete_success)
        mock_delete.assert_called_once()

    async def test_review_action_bans_user(self):
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.REVIEW)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch(
            "engine.moderation_enforcer.execute_delete_message", new_callable=AsyncMock, return_value=True
        ) as mock_delete:
            with patch(
                "engine.moderation_enforcer.execute_ban_user", new_callable=AsyncMock, return_value=True
            ) as mock_ban:
                result = await enforce_moderation(
                    self.event,
                    "940513526",
                    "1367309651",
                    "12345",
                    cap,
                    nsfw,
                    promo,
                    merged,
                    enforcement_enabled=True,
                    dao=self.dao,
                )

        self.assertFalse(result.dry_run)
        self.assertTrue(result.ban_attempted)
        self.assertTrue(result.ban_success)
        self.assertTrue(result.delete_attempted)
        self.assertTrue(result.delete_success)
        mock_ban.assert_called_once()
        mock_delete.assert_called_once()

    async def test_ignore_action_skips_execution(self):
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.IGNORE)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch("engine.moderation_enforcer.execute_delete_message", new_callable=AsyncMock) as mock_delete:
            with patch("engine.moderation_enforcer.execute_ban_user", new_callable=AsyncMock) as mock_ban:
                result = await enforce_moderation(
                    self.event,
                    "940513526",
                    "1367309651",
                    "12345",
                    cap,
                    nsfw,
                    promo,
                    merged,
                    enforcement_enabled=True,
                    dao=self.dao,
                )

        self.assertFalse(result.dry_run)
        self.assertEqual(result.final_action, "ignore")
        mock_delete.assert_not_called()
        mock_ban.assert_not_called()

    async def test_evidence_written_to_violation_table(self):
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.DELETE)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch("engine.moderation_enforcer.execute_delete_message", new_callable=AsyncMock, return_value=True):
            result = await enforce_moderation(
                self.event,
                "940513526",
                "1367309651",
                "12345",
                cap,
                nsfw,
                promo,
                merged,
                enforcement_enabled=True,
                dao=self.dao,
            )

        self.assertTrue(result.evidence_written)
        self.assertIsNotNone(result.violation_id)
        self.assertGreater(result.violation_id, 0)

    async def test_action_failure_records_failed_in_violation(self):
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.DELETE)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch("engine.moderation_enforcer.execute_delete_message", new_callable=AsyncMock, return_value=False):
            result = await enforce_moderation(
                self.event,
                "940513526",
                "1367309651",
                "12345",
                cap,
                nsfw,
                promo,
                merged,
                enforcement_enabled=True,
                dao=self.dao,
            )

        self.assertFalse(result.delete_success)
        self.assertEqual(result.final_action, "delete_failed")

    async def test_dry_run_records_action_taken_correctly(self):
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.DELETE)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch("engine.moderation_enforcer.execute_delete_message", new_callable=AsyncMock):
            result = await enforce_moderation(
                self.event,
                "940513526",
                "1367309651",
                "12345",
                cap,
                nsfw,
                promo,
                merged,
                enforcement_enabled=False,
                dao=self.dao,
            )

        self.assertTrue(result.evidence_written)
        self.assertEqual(result.final_action, "dryrun_delete")

    async def test_no_dao_still_works(self):
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.IGNORE)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch("engine.moderation_enforcer.execute_delete_message", new_callable=AsyncMock):
            result = await enforce_moderation(
                self.event,
                "940513526",
                "1367309651",
                "12345",
                cap,
                nsfw,
                promo,
                merged,
                enforcement_enabled=False,
                dao=None,
            )

        self.assertFalse(result.evidence_written)
        self.assertEqual(result.final_action, "dryrun_ignore")

    async def test_review_escalates_to_kick_after_2_violations(self):
        """review 动作在 24h 内有 2 次违规时自动升级为 kick。"""
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.REVIEW)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch(
            "engine.moderation_enforcer.execute_delete_message", new_callable=AsyncMock, return_value=True
        ) as mock_delete:
            with patch(
                "engine.moderation_enforcer.execute_kick_user", new_callable=AsyncMock, return_value=True
            ) as mock_kick:
                with patch("engine.moderation_enforcer.execute_ban_user", new_callable=AsyncMock) as mock_ban:
                    with patch.object(
                        self.dao, "count_user_violations_since", new_callable=AsyncMock, return_value=2
                    ) as mock_count:
                        result = await enforce_moderation(
                            self.event,
                            "940513526",
                            "1367309651",
                            "12345",
                            cap,
                            nsfw,
                            promo,
                            merged,
                            enforcement_enabled=True,
                            dao=self.dao,
                        )

        self.assertTrue(result.kick_attempted)
        self.assertTrue(result.kick_success)
        self.assertFalse(result.ban_attempted)
        self.assertTrue(result.delete_attempted)
        mock_kick.assert_called_once()
        mock_ban.assert_not_called()

    async def test_review_bans_if_less_than_2_violations(self):
        """review 动作在 24h 内少于 2 次时走 ban，不过渡到 kick。"""
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.REVIEW)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch(
            "engine.moderation_enforcer.execute_delete_message", new_callable=AsyncMock, return_value=True
        ) as mock_delete:
            with patch("engine.moderation_enforcer.execute_kick_user", new_callable=AsyncMock) as mock_kick:
                with patch(
                    "engine.moderation_enforcer.execute_ban_user", new_callable=AsyncMock, return_value=True
                ) as mock_ban:
                    with patch.object(
                        self.dao, "count_user_violations_since", new_callable=AsyncMock, return_value=1
                    ) as mock_count:
                        result = await enforce_moderation(
                            self.event,
                            "940513526",
                            "1367309651",
                            "12345",
                            cap,
                            nsfw,
                            promo,
                            merged,
                            enforcement_enabled=True,
                            dao=self.dao,
                        )

        self.assertTrue(result.ban_attempted)
        self.assertTrue(result.ban_success)
        self.assertFalse(result.kick_attempted)
        self.assertTrue(result.delete_attempted)
        mock_ban.assert_called_once()
        mock_kick.assert_not_called()

    async def test_kick_action_executes_kick(self):
        """当 merger 输出 kick 时，实际执行 kick。"""
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.KICK)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch(
            "engine.moderation_enforcer.execute_kick_user", new_callable=AsyncMock, return_value=True
        ) as mock_kick:
            result = await enforce_moderation(
                self.event,
                "940513526",
                "1367309651",
                "12345",
                cap,
                nsfw,
                promo,
                merged,
                enforcement_enabled=True,
                dao=self.dao,
            )

        self.assertTrue(result.kick_attempted)
        self.assertTrue(result.kick_success)
        mock_kick.assert_called_once()

    async def test_dry_run_kick_logs_would_kick(self):
        """dry-run 模式下 kick 动作只记录 would_kick。"""
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.KICK)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch("engine.moderation_enforcer.execute_kick_user", new_callable=AsyncMock) as mock_kick:
            result = await enforce_moderation(
                self.event,
                "940513526",
                "1367309651",
                "12345",
                cap,
                nsfw,
                promo,
                merged,
                enforcement_enabled=False,
                dao=self.dao,
            )

        self.assertTrue(result.dry_run)
        self.assertEqual(result.final_action, "dryrun_kick")
        mock_kick.assert_not_called()

    async def test_delete_failed_does_not_raise(self):
        cap = self._make_caption()
        nsfw = self._make_result(SuggestedAction.DELETE)
        promo = self._make_result(SuggestedAction.IGNORE)
        merged = nsfw

        with patch("engine.moderation_enforcer.execute_delete_message", new_callable=AsyncMock, return_value=False):
            result = await enforce_moderation(
                self.event,
                "940513526",
                "1367309651",
                "12345",
                cap,
                nsfw,
                promo,
                merged,
                enforcement_enabled=True,
                dao=self.dao,
            )

        self.assertEqual(result.final_action, "delete_failed")
        self.assertTrue(result.evidence_written)
