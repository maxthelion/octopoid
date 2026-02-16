# DEPRECATED: Moved to packages/client/agents/*/scripts/

This directory is kept for backward compatibility during migration.

New agent instances should use the agent directory structure:
- `packages/client/agents/implementer/scripts/`
- `packages/client/agents/gatekeeper/scripts/`
- `.octopoid/agents/<agent-name>/scripts/`

The scheduler will fall back to this directory if the agent directory doesn't have a scripts folder.
