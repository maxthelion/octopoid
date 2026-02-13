# Queue Triage: Stale Issues After Agent Architecture Refactor

**Status:** Idea
**Captured:** 2026-02-13

## Raw

> "Can you read the tasks for all the incoming work. Many of them might have been created before the big refactor and have conflicting requirements re current state of play."

## Summary

All 7 incoming tasks + 1 claimed task were created by the github-issue-monitor from issues written during/before the v2.0 migration. Many reference the **TypeScript client** (`packages/client/src/`) which still exists but is no longer the active agent architecture. Agents now run via **Python orchestrator + `claude -p` scripts**. Several issues describe problems that no longer exist or propose solutions targeting dead code.

## Per-Issue Analysis

### GH-13 — execution_notes field (CLAIMED, currently being worked on)
**Status:** Still valid but misdirected
**Issue references:** TypeScript `base-agent.ts`, `submitTaskCompletion()`, `taskLog()/debugLog()` methods
**Reality:** Agents now run as `claude -p` processes with shell scripts (`finish`, `fail`, `record-progress`). There is no TypeScript agent class to add `generateExecutionSummary()` to.
**Recommendation:** The *concept* is valid (agents should populate execution_notes). But the implementation must target the **script-based architecture**: the `finish` or `submit-pr` script should accept/generate a summary and POST it via the SDK. Close the issue and rewrite as a new one, or update the issue body.

### GH-11 — File path inconsistency (flat vs subdirectory)
**Status:** Likely resolved / obsolete
**Issue references:** `client/src/commands/enqueue.ts`, `client/src/queue-utils.ts`, `client/src/roles/breakdown.ts`, file-based queue subdirectories
**Reality:** The DB purge removed all file-based queue management. Tasks live on the server (D1). There are no local task files to be inconsistent. The `queue-utils.ts` still exists in the TS client but the active code path is the Python SDK talking to the API.
**Recommendation:** **Close the issue.** The problem no longer exists in the active architecture. If there are file path issues in the TS client, they're in dead code.

### GH-10 — Breakdown depth tracking
**Status:** Concept valid, implementation target wrong
**Issue references:** `roles/breakdown.ts` (TypeScript), SQL ALTER TABLE
**Reality:** Breakdown role exists in Python (`orchestrator/roles/breakdown.py`, 446 lines) and is substantially different from the TS version. The DB schema change would need to be a D1 migration on the Cloudflare server, not a local ALTER TABLE.
**Recommendation:** **Update the issue** to target the Python breakdown role and the server schema. The core safety rail (prevent infinite re-breakdown) is still needed.

### GH-9 — Debugging/observability endpoints
**Status:** Still valid, mostly architecture-neutral
**Issue references:** Server API endpoints (still correct), CLI integration
**Reality:** The server endpoints proposal is still accurate — these would be new routes in the Cloudflare Workers server. The CLI part references `octopoid debug` commands that don't exist yet in either TS or Python.
**Recommendation:** **Keep as-is.** This is a server-side feature request and largely unaffected by the refactor. Minor cleanup: remove references to v1.x `status.py`.

### GH-8 — Init UX improvements
**Status:** Still valid
**Issue references:** `octopoid init` command behaviour
**Reality:** The init command exists in both TypeScript (`packages/client/src/commands/init.ts`) and Python (`orchestrator/init.py`). The UX issues described are real — no mode selection prompt, no next-steps guidance. An agent already submitted a PR for this (GH-8 PR #14, merged).
**Recommendation:** **Check if PR #14 addressed this.** If partially fixed, update the issue with remaining gaps.

### GH-7 — Command whitelist for IDE permissions
**Status:** Partially relevant, needs rethink
**Issue references:** `config.yaml` commands section, `octopoid permissions export`
**Reality:** Agents now run as `claude -p` with explicit `--allowedTools` flags set by the scheduler (`invoke_claude()` in `scheduler.py`). The permission model is fundamentally different — it's not about IDE prompts, it's about what tools the spawned Claude process can use. The `.claude/settings.local.json` already has allowed commands.
**Recommendation:** **Rewrite.** The real need is: (1) agent scripts should declare what tools they need, (2) the scheduler should configure `--allowedTools` per role, (3) users running the *scheduler itself* from Claude Code need their IDE permissions configured. The current issue conflates agent permissions with user IDE permissions.

### GH-4 — init --local doesn't create agents.yaml
**Status:** Likely still valid
**Issue references:** `octopoid init --local` missing agents.yaml
**Reality:** `orchestrator/init.py` has `EXAMPLE_AGENTS_YAML` template. Need to verify whether the init command actually writes it. The per-agent config files feature request is over-engineered for now — the current `agents.yaml` works fine with 2-3 agents.
**Recommendation:** **Keep the bug report, drop the feature request.** Verify whether init now creates agents.yaml. If not, fix it.

### GH-3 — Per-task logs and status script
**Status:** Concept valid, implementation target completely wrong
**Issue references:** `.orchestrator/logs/tasks/` (v1 path), `create_task()`, `submit_completion()`, `accept_completion()`, `review_reject_task()` — all TypeScript functions
**Reality:** Task lifecycle is now: server API creates task → scheduler claims via SDK → `claude -p` runs in worktree → scripts (`finish`/`fail`) update task via SDK. Logging would need to happen either server-side (audit log in D1) or in the scheduler (Python). The TS functions referenced don't run anymore.
**Recommendation:** **Rewrite.** The need for task lifecycle audit logs is real and important. But implementation should be: (a) server-side audit log endpoint that records every state transition, or (b) scheduler-side logging in Python when it detects task completion/failure.

## Recommendations Summary

| Issue | Action | Reason |
|-------|--------|--------|
| GH-3  | **Rewrite** | References dead TS code, but need is real |
| GH-4  | **Verify & keep** | Bug may still exist |
| GH-7  | **Rewrite** | Permission model changed fundamentally |
| GH-8  | **Check PR #14** | May already be fixed |
| GH-9  | **Keep** | Server-side, mostly unaffected |
| GH-10 | **Update** | Target Python role + D1 migration |
| GH-11 | **Close** | Problem no longer exists |
| GH-13 | **Rewrite** | Target script architecture, not TS classes |

## Proposed Actions

1. **Close GH-11** with comment explaining the file-based queue is gone
2. **Close GH-8** if PR #14 covered it (or update with remaining gaps)
3. **Update GH-10** to reference Python code and D1 migrations
4. **Rewrite GH-3, GH-7, GH-13** to target current architecture
5. **Remove stale tasks** from queue for closed/rewritten issues and re-create from updated issues
6. **Reprioritize**: GH-9 (observability) is genuinely high priority; GH-13 and GH-3 are low priority polish

## Open Questions

- Should we close all issues and re-create fresh ones? Or update in place?
- Should the github-issue-monitor re-sync after issues are updated, or do we manually manage tasks?
- Are there issues we should close outright as "won't fix" given the architecture change?
