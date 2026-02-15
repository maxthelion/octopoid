# refactor-12: Migrate our config and clean up old files

ROLE: implement
PRIORITY: P2
BRANCH: feature/client-server-architecture
CREATED: 2026-02-15T00:00:00Z
CREATED_BY: human
SKIP_PR: true
DEPENDS_ON: refactor-09, refactor-10, refactor-11

## Context

This is the final task in the scheduler refactor project. All the infrastructure is in place:
- Scheduler pipeline architecture (refactor-01 through refactor-06)
- Agent directory templates (refactor-07, refactor-08)
- Init scaffolding (refactor-09)
- Fleet config format (refactor-10)
- Spawn strategy integration (refactor-11)

Now we migrate octopoid's own config to use the new system and clean up superseded files.

Reference: `project-management/drafts/9-2026-02-15-agent-directories.md` (What Moves Where table)

## What to do

### 1. Scaffold our agent directories

Copy the product templates into our project:

```bash
# From project root
cp -r packages/client/agents/implementer/ .octopoid/agents/implementer/
cp -r packages/client/agents/gatekeeper/ .octopoid/agents/gatekeeper/
```

Do NOT touch `.octopoid/agents/github-issue-monitor/` -- that's our custom agent, not from a template.

After this step, `.octopoid/agents/` should contain:
```
.octopoid/agents/
  implementer/
    agent.yaml
    prompt.md
    instructions.md
    scripts/
  gatekeeper/
    agent.yaml
    prompt.md
    instructions.md
    scripts/
  github-issue-monitor/    # existing custom agent, unchanged
    state.json
    exit_code
```

### 2. Update our .octopoid/agents.yaml to new fleet format

Replace the current agents.yaml content:

```yaml
# Current format
paused: false
queue_limits:
  max_claimed: 3
  max_incoming: 20
  max_open_prs: 10
agents:
  - id: 1
    name: implementer-1
    role: implementer
    ...
```

With the new fleet format:

```yaml
paused: false

queue_limits:
  max_claimed: 3
  max_incoming: 20
  max_open_prs: 10

fleet:
  - name: implementer-1
    type: implementer
    enabled: true

  - name: implementer-2
    type: implementer
    enabled: true

  - name: github-issue-monitor
    type: custom
    path: .octopoid/agents/github-issue-monitor/
    enabled: true
    interval_seconds: 900
    lightweight: true
```

### 3. Create agent.yaml for github-issue-monitor

The github-issue-monitor doesn't have an agent.yaml yet (it's a custom agent, not from a template). Create a minimal one:

```yaml
# .octopoid/agents/github-issue-monitor/agent.yaml
role: github_issue_monitor
model: sonnet
interval_seconds: 900
lightweight: true
spawn_mode: module
```

This allows the fleet config to load its defaults correctly.

### 4. Clean up old files that have been moved

Before deleting anything, verify the files are no longer imported or referenced:

#### Check before deleting

Run these checks for each file:

```bash
# Check for imports/references
grep -r "orchestrator/prompts/implementer" --include="*.py" --include="*.ts" --include="*.md" .
grep -r "commands/agent/implement.md" --include="*.py" --include="*.ts" --include="*.md" .
grep -r "agent_scripts" --include="*.py" .
```

#### Files to consider removing

| File | Moved to | Check before deleting |
|------|----------|----------------------|
| `orchestrator/prompts/implementer.md` | `packages/client/agents/implementer/prompt.md` + `.octopoid/agents/implementer/prompt.md` | Check `orchestrator/prompt_renderer.py` -- does it still reference this path? If refactor-11 updated it to use agent_dir, safe to delete. |
| `commands/agent/implement.md` | `packages/client/agents/implementer/instructions.md` + `.octopoid/agents/implementer/instructions.md` | Check `generate_agent_instructions()` in scheduler.py |
| `orchestrator/agent_scripts/*` | `packages/client/agents/implementer/scripts/` + `.octopoid/agents/implementer/scripts/` | Check `prepare_task_directory()` -- does it still fallback to this path? |

**IMPORTANT:** If any file is still referenced by fallback code (legacy paths in refactor-11), do NOT delete it yet. The fallback paths exist for backward compatibility during migration. Only delete files that have zero remaining references.

If in doubt, keep the file and add a comment: `# DEPRECATED: Moved to packages/client/agents/implementer/prompt.md`

### 5. Verify everything works

1. Run the scheduler with --debug --once:
   ```bash
   python -m orchestrator.scheduler --debug --once
   ```
   Verify:
   - Config loads correctly from fleet format
   - Agent directories are found
   - No FileNotFoundError or missing config errors

2. Run all tests:
   ```bash
   pytest tests/
   ```
   All tests must pass.

3. Check the debug log for any warnings about missing files or config.

### What NOT to do

- Do NOT modify `.octopoid/agents/github-issue-monitor/` contents (except adding agent.yaml)
- Do NOT delete files that are still referenced (check imports first)
- Do NOT break backward compatibility for other octopoid users who haven't migrated yet
- Do NOT modify test files to make them pass -- fix the actual code instead

## Key files

- `.octopoid/agents/` -- scaffold our agent directories here
- `.octopoid/agents.yaml` -- update to fleet format
- `orchestrator/prompts/implementer.md` -- candidate for removal
- `commands/agent/implement.md` -- candidate for removal
- `orchestrator/agent_scripts/` -- candidate for removal
- `project-management/drafts/9-2026-02-15-agent-directories.md` -- design reference (What Moves Where table)

## Acceptance criteria

- [ ] `.octopoid/agents/implementer/` exists with agent.yaml, prompt.md, instructions.md, scripts/
- [ ] `.octopoid/agents/gatekeeper/` exists with agent.yaml, prompt.md, instructions.md, scripts/
- [ ] `.octopoid/agents/github-issue-monitor/` is unchanged (except new agent.yaml)
- [ ] `.octopoid/agents.yaml` uses the new fleet format
- [ ] Scheduler loads config correctly with `--debug --once`
- [ ] Old superseded files are either removed (if no references remain) or marked as deprecated
- [ ] All tests pass (`pytest tests/`)
- [ ] No regressions in scheduler functionality
