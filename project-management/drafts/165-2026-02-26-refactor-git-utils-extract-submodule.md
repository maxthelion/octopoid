# Refactor git_utils.py: extract submodule_utils module

**Author:** codebase-analyst
**Captured:** 2026-02-26

## Analysis

`octopoid/git_utils.py` has grown to 1018 lines and contains at least four
distinct concerns:

1. **Core git primitives** (lines 1–55): `run_git()`, `_add_detached_worktree()`,
   `_remove_worktree()`
2. **Worktree lifecycle** (lines 58–390): `ensure_worktree()`,
   `create_task_worktree()`, `cleanup_task_worktree()`, helpers
3. **Branch / PR operations** (lines 391–680): `create_feature_branch()`,
   `push_branch()`, `create_pull_request()`, `count_open_prs()`,
   `list_open_prs()`, `cleanup_merged_branches()`
4. **Submodule operations** (lines 683–1018): `has_submodule_changes()`,
   `has_uncommitted_submodule_changes()`, `get_submodule_unpushed_commits()`,
   `push_submodule_to_main()`, `stage_submodule_pointer()`,
   `get_submodule_status()`

The submodule block is the most self-contained: all six functions share a common
`submodule_name: str = "orchestrator"` parameter and have no callers in the
worktree or PR code paths. They're only referenced from the scheduler's
submodule-sync job and from `jobs.py`.

## Proposed Split

Extract the six submodule functions (lines 683–1018, ~335 lines) into a new
`octopoid/submodule_utils.py`. Update import sites in `scheduler.py` and
`jobs.py`. The remaining `git_utils.py` shrinks to ~680 lines.

Longer term, the worktree and PR sections could also be separated, but that
requires more import-site changes and should be a follow-on task.

## Complexity

**Low-to-medium.** The extraction is mechanical: move six functions to a new
file, update `from .git_utils import …` calls at the three or four import sites.
No logic changes needed. The test file `tests/test_git_utils.py` (812 lines)
would need its import updated as well. Risk is low — functions have no circular
dependencies on the rest of `git_utils.py`.


## Invariants

No new invariants — this is a pure refactoring of `git_utils.py` to extract a `submodule_utils` module. No behaviour changes.
