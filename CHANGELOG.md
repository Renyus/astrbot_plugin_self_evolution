# Changelog

All notable changes to this project will be documented in this file.

## [2.1.1] - 2026-03-08
### Fixed
- **Cron Job Reliability**: Converted the `SelfEvolution_DailyReflection` from an `active_agent` scheduled job to a `basic` scheduled job. This fixes a critical error in AstrBot v4 where active agent jobs would crash at execution due to a missing/invalid session context on startup. The job now silently sets a global pending flag (`daily_reflection_pending = True`), and the actual reflection is seamlessly injected into the LLM context during the next user interaction.
- **AST Security Validation**: Relaxed the extremely strict programmatic AST security firewall (`_validate_ast_security`) that was blocking the LLM's own legitimate meta-programming proposals. Standard modules like `os`, `sys`, and functions like `open` and `getattr` are now permitted. Safety relies on the fact that proposals are sandboxed and require explicit human-administrator approval before deployment.

## [2.1.0] - 2026-03-08
### Added
- **Evolution Management Commands**: Introduced powerful new administrative tools for managing the self-reflection and evolution queue:
  - `/reject_evolution [ID]`: Deny a specific evolution request and clear it from the queue.
  - `/clear_evolutions`: Mass-clear all pending requests.
  - `/reflect`: Manually trigger an immediate self-reflection cycle without waiting for the scheduled cron job.
- **Plugin Validation Script**: Added a standalone Windows-compatible `validate_plugin.py` to pre-check code syntax, `_conf_schema.json` integrity, and `metadata.yaml` before pushing to production.

### Changed
- Comprehensive documentation overhaul in `README.md` to accurately reflect the newly available commands and the updated administrative workflows.

## [2.0.4] - 2026-03-07
### Fixed
- **Robust Persona Identification**: Resolved a blocker where custom personas (like 'herta') were incorrectly defaulting back to 'default', which barred them from self-evolution. The system now uses AstrBot's official `resolve_selected_persona` method to correctly respect persona inheritance layers (Conversation -> Channel -> Platform -> Global).
- **Aiocqhttp Compatibility**: Replaced direct struct access (`event.persona_id`) with the robust context resolution path to prevent `AttributeError: 'AiocqhttpMessageEvent' object has no attribute 'persona_id'` on specific messaging platforms.

## [2.0.3] - 2026-03-07
### Fixed
- **Admin Permission Check**: Refactored the broken `is_admin` fallback logic. Fixed a "Fail-Safe" issue where the strict absence of IDs in the configuration array `admin_users` blocked global AstrBot administrators from accessing plugin features. The plugin now natively respects `event.is_admin()`.
- **System Configuration Schema**: Added the missing `admin_users` and `allow_meta_programming` definitions to `_conf_schema.json` to enable proper rendering in the AstrBot Web UI.
