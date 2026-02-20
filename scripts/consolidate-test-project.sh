#!/usr/bin/env bash
# Consolidate test project worktree commits onto feature/client-server-architecture.
#
# The 18 project tasks completed work in worktrees but push_branch failed
# (wrong flow assigned). This script cherry-picks all commits onto the branch.
#
# Usage: bash scripts/consolidate-test-project.sh [--dry-run]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TASKS_DIR="$REPO_ROOT/.octopoid/runtime/tasks"
BRANCH="feature/client-server-architecture"
DRY_RUN="${1:-}"

cd "$REPO_ROOT"

# Ensure we're on the right branch and clean
current_branch=$(git branch --show-current)
if [ "$current_branch" != "$BRANCH" ]; then
    echo "ERROR: Not on $BRANCH (on $current_branch)"
    exit 1
fi

# Check for modified tracked files (ignore untracked and submodules)
if [ -n "$(git diff --name-only --ignore-submodules HEAD)" ]; then
    echo "ERROR: Working tree has modified tracked files. Commit or stash first."
    git diff --name-only --ignore-submodules HEAD
    exit 1
fi
if [ -n "$(git diff --cached --name-only)" ]; then
    echo "ERROR: Staged changes exist. Commit or stash first."
    exit 1
fi

echo "=== Consolidating test project worktree commits ==="
echo "Branch: $BRANCH"
echo ""

# Task order matters — earlier batches may be dependencies for later ones.
# Within a batch, order by sequence number.
TASKS=(
    # Batch 1: Docs (1-1 is special — staged but uncommitted)
    "TASK-test-1-1:staged"
    "TASK-test-1-2:cherry-pick"
    "TASK-test-1-3:cherry-pick"
    # Batch 2: Lease/PID/Idempotent
    "TASK-test-2-1:cherry-pick"
    "TASK-test-2-2:cherry-pick"
    "TASK-test-2-3:cherry-pick"
    # Batch 3: Pool/Claim/Priority
    "TASK-test-3-1:cherry-pick"
    "TASK-test-3-2:cherry-pick"
    "TASK-test-3-3:cherry-pick"
    # Batch 4: Fake gh + step failures
    "TASK-test-4-1:cherry-pick"
    "TASK-test-4-2:cherry-pick"
    "TASK-test-4-3:cherry-pick"
    "TASK-test-4-4:cherry-pick"
    # Batch 5: Flow engine
    "TASK-test-5-1:cherry-pick"
    "TASK-test-5-2:cherry-pick"
    "TASK-test-5-3:cherry-pick"
    # Batch 6: Backpressure/Health
    "TASK-test-6-1:cherry-pick"
    "TASK-test-6-2:cherry-pick"
)

SUCCESS=0
FAILED=0
SKIPPED=0

for entry in "${TASKS[@]}"; do
    task_id="${entry%%:*}"
    mode="${entry##*:}"
    wt="$TASKS_DIR/$task_id/worktree"

    if [ ! -d "$wt" ]; then
        echo "SKIP  $task_id — worktree not found"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    if [ "$mode" = "staged" ]; then
        # Special case: 1-1 has files staged but not committed.
        # We need to extract the staged content and commit it here.
        echo ""
        echo "--- $task_id (extract staged files) ---"

        # Get list of staged files
        staged_files=$(cd "$wt" && git diff --cached --name-only)
        if [ -z "$staged_files" ]; then
            echo "SKIP  $task_id — no staged files found"
            SKIPPED=$((SKIPPED + 1))
            continue
        fi

        if [ "$DRY_RUN" = "--dry-run" ]; then
            echo "DRY   Would extract staged files: $staged_files"
            SUCCESS=$((SUCCESS + 1))
            continue
        fi

        # Copy each staged file from the worktree index to our working tree
        while IFS= read -r file; do
            mkdir -p "$(dirname "$file")"
            (cd "$wt" && git show ":$file") > "$file"
            git add "$file"
            echo "      Extracted: $file"
        done <<< "$staged_files"

        # Commit with original task context
        git commit -m "$(cat <<EOF
docs: add testing guide (docs/testing.md)

From TASK-test-1-1: Write testing guide covering philosophy,
fixtures, and patterns for the mock agent test infrastructure.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
        echo "OK    $task_id — committed staged files"
        SUCCESS=$((SUCCESS + 1))

    elif [ "$mode" = "cherry-pick" ]; then
        # Standard cherry-pick of the HEAD commit from the worktree
        commit=$(cd "$wt" && git rev-parse HEAD)
        base=$(cd "$wt" && git merge-base HEAD origin/$BRANCH 2>/dev/null || echo "")

        if [ "$commit" = "$base" ]; then
            echo "SKIP  $task_id — HEAD is already on base (no new commits)"
            SKIPPED=$((SKIPPED + 1))
            continue
        fi

        # Count commits above base
        count=$(cd "$wt" && git rev-list --count ${base}..HEAD 2>/dev/null || echo "0")
        msg=$(cd "$wt" && git log --oneline -1 HEAD)

        echo ""
        echo "--- $task_id ($count commit(s)) ---"
        echo "      $msg"

        if [ "$DRY_RUN" = "--dry-run" ]; then
            echo "DRY   Would cherry-pick $commit"
            SUCCESS=$((SUCCESS + 1))
            continue
        fi

        if git cherry-pick "$commit" --no-edit 2>/dev/null; then
            echo "OK    $task_id — cherry-picked successfully"
            SUCCESS=$((SUCCESS + 1))
        else
            echo "FAIL  $task_id — cherry-pick conflict!"
            echo "      Resolve conflicts, then: git cherry-pick --continue"
            echo "      Or skip: git cherry-pick --abort"
            FAILED=$((FAILED + 1))
            # Stop on first conflict so user can resolve
            echo ""
            echo "=== STOPPED at $task_id due to conflict ==="
            echo "Results so far: $SUCCESS OK, $FAILED FAILED, $SKIPPED SKIPPED"
            exit 1
        fi
    fi
done

echo ""
echo "=== Done ==="
echo "$SUCCESS OK, $FAILED FAILED, $SKIPPED SKIPPED"
echo ""
if [ "$DRY_RUN" = "--dry-run" ]; then
    echo "This was a dry run. Run without --dry-run to apply."
fi
