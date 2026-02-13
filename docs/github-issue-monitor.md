# GitHub Issue Monitor Agent

The GitHub Issue Monitor is a custom agent that automatically polls GitHub issues and creates tasks for new issues.

## Features

- **Automatic task creation**: Creates a task for each new GitHub issue
- **Duplicate prevention**: Uses issue number to avoid creating duplicate tasks
- **Label-based prioritization**: Sets task priority based on issue labels
- **Comments on issues**: Adds a comment to the GitHub issue noting that a task was created
- **State tracking**: Maintains state to remember which issues have been processed

## Prerequisites

1. **GitHub CLI (`gh`)**: Must be installed and authenticated
   ```bash
   # Install gh CLI
   brew install gh  # macOS
   # or download from https://cli.github.com/

   # Authenticate
   gh auth login
   ```

2. **Repository access**: The agent needs access to the GitHub repository
   ```bash
   # Verify gh works in your project
   cd /path/to/your/project
   gh issue list
   ```

## Configuration

Add the agent to your `.octopoid/agents.yaml`:

```yaml
agents:
  # ... your existing agents ...

  # GitHub issue monitor (runs every 5 minutes)
  - id: 101
    name: github-issue-monitor
    role: github_issue_monitor
    enabled: true
    interval_seconds: 300  # Poll every 5 minutes
    max_concurrent: 1
```

### Configuration Options

- **`interval_seconds`**: How often to poll for new issues (default: 300 = 5 minutes)
  - Set to `60` for 1 minute polling
  - Set to `900` for 15 minute polling
  - Set to `3600` for hourly polling

- **`max_concurrent`**: Should always be `1` (only one instance should run at a time)

## How It Works

1. **Polling**: Every `interval_seconds`, the agent fetches all open GitHub issues using `gh issue list`

2. **State tracking**: The agent maintains a state file at `.octopoid/runtime/github_issues_state.json` with the list of processed issue numbers

3. **New issue detection**: Compares current issues against processed issues to find new ones

4. **Task creation**: For each new issue, creates a task with:
   - **Title**: `[GH-{number}] {issue title}`
   - **Role**: `implement` (can be customized based on labels)
   - **Priority**: Based on issue labels:
     - `P0` if labeled with `urgent`, `critical`, or `P0`
     - `P2` if labeled with `low-priority` or `P2`
     - `P1` (default) otherwise
   - **Context**: Issue description, URL, and labels
   - **Acceptance criteria**:
     - Resolve GitHub issue #{number}
     - All tests pass
     - Code follows project conventions

5. **Issue comment**: Adds a comment to the GitHub issue:
   ```
   🤖 Octopoid has automatically created task `task-abc123` for this issue.

   The task is now in the queue and will be picked up by an available agent.
   ```

6. **State update**: Marks the issue as processed so it won't be duplicated

## Priority Mapping

The agent maps GitHub issue labels to task priorities:

| Issue Labels | Task Priority |
|--------------|---------------|
| `urgent`, `critical`, `P0` | P0 |
| `low-priority`, `P2` | P2 |
| (default) | P1 |

## Role Mapping

All issues currently map to the `implement` role, but you can customize this in the code:

```python
# In orchestrator/roles/github_issue_monitor.py
# Determine role from labels
role = "implement"  # default
if any(label in ["bug", "fix"] for label in labels):
    role = "implement"
elif any(label in ["documentation", "docs"] for label in labels):
    role = "implement"  # or create a custom "docs" role
elif any(label in ["enhancement", "feature"] for label in labels):
    role = "breakdown"  # for complex features
```

## State File

The agent maintains state at `.octopoid/runtime/github_issues_state.json`:

```json
{
  "processed_issues": [1, 5, 12, 23, 45]
}
```

- **Never commit this file** (already in `.gitignore`)
- To reset and reprocess all issues: delete this file
- The file is automatically created on first run

## Example Workflow

1. **User creates GitHub issue**:
   ```
   Title: Add dark mode support
   Labels: enhancement, P1
   Body: Users want a dark mode toggle in settings
   ```

2. **Agent detects new issue** (within 5 minutes):
   ```
   [github-issue-monitor] Found 3 open issues
   [github-issue-monitor] Processing new issue #23: Add dark mode support
   [github-issue-monitor] Created task for issue #23: task-dark-mode-123.md
   ```

3. **Task created** in `.octopoid/runtime/shared/queue/incoming/`:
   ```markdown
   ---
   id: task-dark-mode-123
   role: implement
   priority: P1
   created_by: github-issue-monitor
   ---

   # [GH-23] Add dark mode support

   ## Context

   **GitHub Issue:** [23](https://github.com/org/repo/issues/23)

   **Description:**
   Users want a dark mode toggle in settings

   **Labels:** enhancement, P1

   ## Acceptance Criteria

   - [ ] Resolve GitHub issue #23
   - [ ] All tests pass
   - [ ] Code follows project conventions
   ```

4. **Issue commented** by agent:
   ```
   🤖 Octopoid has automatically created task `task-dark-mode-123` for this issue.

   The task is now in the queue and will be picked up by an available agent.
   ```

5. **Regular implementer picks up task** and works on it

6. **When task completes**, the PR references the issue:
   ```
   Closes #23

   Implemented dark mode toggle in settings panel.
   ```

## Troubleshooting

### Agent not creating tasks

1. **Check gh CLI is installed and authenticated**:
   ```bash
   gh auth status
   gh issue list
   ```

2. **Check agent logs**:
   ```bash
   tail -f .octopoid/runtime/logs/github-issue-monitor-*.log
   ```

3. **Check state file**:
   ```bash
   cat .octopoid/runtime/github_issues_state.json
   ```

### Tasks created for old issues

If you want to only create tasks for new issues going forward:

1. **Prepopulate state file** with existing issue numbers:
   ```bash
   gh issue list --state open --json number --jq '[.[] | .number]' > temp.json
   echo "{\"processed_issues\": $(cat temp.json)}" > .octopoid/runtime/github_issues_state.json
   rm temp.json
   ```

2. **Restart the agent**

### Agent timing out

If you have many issues (>100), increase the timeout in the code:

```python
# In orchestrator/roles/github_issue_monitor.py
result = subprocess.run(
    [...],
    timeout=60,  # Increase from 30 to 60 seconds
)
```

## Customization

### Custom acceptance criteria

Edit the `create_task_from_issue` method:

```python
acceptance_criteria = [
    f"Resolve GitHub issue #{issue_number}",
    "All tests pass",
    "Code follows project conventions",
    "Documentation updated",  # Add this
    "Screenshots provided for UI changes",  # Add this
]
```

### Custom role mapping

Add more sophisticated role detection:

```python
# Determine role from labels
if "bug" in labels or "fix" in labels:
    role = "implement"
elif "docs" in labels or "documentation" in labels:
    role = "implement"  # or create custom docs role
elif "breaking-change" in labels:
    role = "breakdown"  # complex changes need breakdown
elif "good-first-issue" in labels:
    priority = "P2"  # deprioritize for new contributors
else:
    role = "implement"
```

### Skip certain labels

Ignore issues with specific labels:

```python
# In the run() method, before processing:
skip_labels = ["wontfix", "duplicate", "on-hold"]
if any(label["name"] in skip_labels for label in issue.get("labels", [])):
    self.debug_log(f"Skipping issue #{issue_number} (has skip label)")
    processed_issues.add(issue_number)  # Mark as processed to not check again
    continue
```

## Advanced: Multi-Repository Monitoring

To monitor multiple repositories, create separate agent instances:

```yaml
agents:
  - id: 101
    name: github-monitor-main
    role: github_issue_monitor
    enabled: true
    interval_seconds: 300
    max_concurrent: 1
    env:
      GH_REPO: "org/main-repo"

  - id: 102
    name: github-monitor-docs
    role: github_issue_monitor
    enabled: true
    interval_seconds: 600  # Less frequent for docs repo
    max_concurrent: 1
    env:
      GH_REPO: "org/docs-repo"
```

Then update the agent to use the `GH_REPO` environment variable:

```python
# In fetch_github_issues():
repo = os.environ.get("GH_REPO", "")
cmd = ["gh", "issue", "list", "--state", "open", ...]
if repo:
    cmd.extend(["--repo", repo])
```

## FAQ

**Q: Will this create duplicate tasks if I restart the agent?**
A: No, the state file persists across restarts.

**Q: What if I delete an issue?**
A: The task remains in the queue. You'll need to manually close it.

**Q: Can I run this agent manually?**
A: Yes, but it's designed to run as part of the scheduler.

**Q: Does this work with private repositories?**
A: Yes, as long as `gh` is authenticated with appropriate permissions.

**Q: Can I customize the comment posted to issues?**
A: Yes, edit the `_comment_on_issue` method in the agent code.
