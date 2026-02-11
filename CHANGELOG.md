# Changelog

All notable changes to Octopoid will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Unified configuration system (.octopoid/config.yaml) as single source of truth
- Dashboard now requires API server connection (v2.0 mode)
- GitHub issue monitor agent for automatic task creation from issues
- Production agents.yaml configuration with implementers and issue monitor
- SDK methods for task operations (create, claim, submit, delete, update)
- Title field to tasks table for better display in dashboards and UIs
- DELETE endpoint for tasks (API cleanup support)
- Cleanup script for removing test data (scripts/cleanup-test-data.py)
- DEVELOPMENT_RULES.md with guidelines for testing and database operations
- v2.0 API-only architecture rule (no database/file queue modes)

### Changed
- Dashboard is now API-only, removed local database mode
- Installation documentation updated to reflect source-only installation
- README updated with comprehensive troubleshooting section
- GitHub issue monitor now uses SDK to register tasks with API server
- Agents now use unified .octopoid/config.yaml for server configuration
- **BREAKING**: v2.0 is API-only architecture - no database mode, no file-based queue mode
- queue_utils.py refactored to API-only architecture (Phases 1-8 complete)
  - ✅ Phase 1: Foundation functions (get_task_by_id, list_tasks, find_task_by_id)
  - ✅ Phase 2: Critical path (claim_task, submit_completion, accept/reject_completion)
  - ✅ Phase 3: State management (fail_task, retry_task, reset_task, hold_task, mark_needs_continuation, resume_task)
  - ✅ Phase 4: Creation/deletion (create_task, complete_task, reject_task)
  - ✅ Phase 5: Review/feedback (already done in Phase 2)
  - ✅ Phase 6: Backpressure (separate module, not in queue_utils)
  - ✅ Phase 7: Complex workflows (use refactored functions, already API-compatible)
  - ✅ Phase 8: Projects (create_project, get_project, list_projects)
  - ✅ Additional: count_queue() now uses list_tasks (SDK)
  - 📝 Note: Some helper functions still have is_db_enabled() checks for backward compatibility
  - 📝 Note: Implementers can now claim and work on tasks via API (critical path complete)

### Fixed
- Dashboard now correctly connects to API server via SDK
- Reports module works in API-only mode (added missing Optional import)
- GitHub issue monitor creates tasks that are visible in API-connected dashboard

---

## Instructions for Agents

When you complete a task, add an entry to the **Unreleased** section above under the appropriate category:

- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** for vulnerability fixes

**Format:**
```markdown
- Brief description of change (#PR-number if applicable)
```

**Example:**
```markdown
### Added
- Task-specific logging system for better observability (#123)

### Fixed
- GitHub issue monitor now uses SDK instead of local files (#124)
```

Keep entries concise and user-focused. Focus on WHAT changed, not HOW.
