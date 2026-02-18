---
**Processed:** 2026-02-18
**Mode:** human-guided
**Actions taken:**
- Archived as Superseded. Feature branch has grown far beyond original scope (flows, detached HEAD, backpressure rewrite, etc). Several prerequisites failed or closed unmerged. A fresh merge plan will be needed when the time comes.
**Outstanding items:** none — will create new draft when ready to merge
---

# Merge plan: refactor → feature → main

**Status:** Superseded
**Captured:** 2026-02-16

## Two-step merge

### Step 1: Merge refactor work into feature/client-server-architecture

The refactor tasks are on `agent/TASK-debc30fd` (and agent PR branches off that). Once the task pipeline completes, this work needs to merge into `feature/client-server-architecture`.

**Tasks that must land first:**
1. **TASK-7a393cef** — queue_utils entity module split (in progress, 3rd review round)
2. **TASK-082c8162** — SDK ProjectsAPI + rewrite projects.py (blocked by 1)
3. **TASK-334e15ee** — Worktree lifecycle: detach instead of delete (blocked by 2)
4. **TASK-1597e6f5** — Integration test for project lifecycle (blocked by 3)
5. **TASK-ad3a4e7a** — Sanity-check gatekeeper (blocked by 1)

Each task creates a PR that merges into `feature/client-server-architecture`. Once all 5 are merged, step 1 is done.

**Should also resolve before step 2:**
- **gh-7-e92832d1** — IDE permission whitelist (PR #35, in provisional)
- **10 failed tasks** — triage: are any still relevant or all superseded?

**Cleanup:**
- Clean up orphaned worktrees (36 exist)
- Remove temp/dev artifacts from the branch (TEST_STATUS.md etc)

### Step 2: Merge feature/client-server-architecture to main

Once the feature branch is clean and all refactor work is integrated:

**Branch state:** 99+ commits ahead of main, main has 0 divergent commits. Clean fast-forward possible.

**What this brings to main:**
- Local DB removed (`db.py` -2,189 lines). API-only mode.
- Script-based agent architecture
- Hook system (BEFORE_SUBMIT, BEFORE_MERGE)
- Scheduler rewrite (state-first pattern, lease-based claims)
- Entity module split (queue_utils → 7 focused modules)
- Cloudflare Workers server + D1 database
- Python SDK + TypeScript client
- Integration tests (27 tests)
- Sanity-check gatekeeper
- 8 legacy test files deleted (~2,670 lines)
- Stats: 290 files changed, +36,807 / -18,946

**Merge strategy:** Merge commit (`git merge --no-ff`) — clear landmark in git log for "v2.0 architecture" while preserving individual commits for blame.

**Post-merge:**
- Tag as `v2.0.0`
- Continue adhoc tasks on main going forward
- Keep feature branch for reference (per CLAUDE.md: never delete branches)

## Open questions

- Can any of the 5 blocked tasks (e.g. gatekeeper) be deferred to post-merge on main instead?
- Final manual smoke test before step 2?
- Any project-management docs that need updating before main merge?
