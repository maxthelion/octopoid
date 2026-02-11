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
- SDK methods for task creation, claiming, submission, and deletion (create, claim, submit, delete)
- Title field to tasks table for better display in dashboards and UIs
- DELETE endpoint for tasks (API cleanup support)
- Cleanup script for removing test data (scripts/cleanup-test-data.py)
- DEVELOPMENT_RULES.md with guidelines for testing and database operations

### Changed
- Dashboard is now API-only, removed local database mode
- Installation documentation updated to reflect source-only installation
- README updated with comprehensive troubleshooting section
- GitHub issue monitor now uses SDK to register tasks with API server
- Agents now use unified .octopoid/config.yaml for server configuration

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
