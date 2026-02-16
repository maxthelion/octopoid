# Scheduler Refactor: Acceptance Criteria

**Status:** Idea
**Captured:** 2026-02-16
**Source drafts:** 9 (Agent Directories), 10 (Scheduler Refactor)

## 1. Scheduler Pipeline Structure

### 1.1 `run_scheduler()` is a thin orchestrator

- [ ] ~30-60 lines, no business logic
- [ ] Three phases visible at a glance: housekeeping → evaluate → spawn
- [ ] No nested `if/continue` guard chains

**Verify:** Read `orchestrator/scheduler.py`, find `def run_scheduler`. It should fit on one screen.

### 1.2 `AgentContext` dataclass

- [ ] Holds: `agent_config`, `agent_name`, `role`, `interval`, `state`, `state_path`, `claimed_task`
- [ ] Used by all guard functions and spawn strategies (no loose local variables)

**Verify:** `grep -n "AgentContext" orchestrator/scheduler.py` — should appear in guards, spawn strategies, and `run_scheduler`.

### 1.3 Guard chain

- [ ] `AGENT_GUARDS` list exists with guards in order: enabled → not_running → interval → backpressure → pre_check → claim_task
- [ ] Each guard is a standalone function: `(ctx: AgentContext) -> tuple[bool, str]`
- [ ] `evaluate_agent(ctx)` iterates the chain, stops on first `False`
- [ ] No guard logic remains inline in `run_scheduler()`

**Verify:**
```bash
grep "^def guard_" orchestrator/scheduler.py    # should list 6 guards
grep "AGENT_GUARDS" orchestrator/scheduler.py   # should show the list
```

### 1.4 Housekeeping jobs

- [ ] `HOUSEKEEPING_JOBS` list exists
- [ ] `run_housekeeping()` iterates with try/except per job (fault-isolated)
- [ ] A failing job logs the error and continues to the next

**Verify:**
```bash
grep "HOUSEKEEPING_JOBS" orchestrator/scheduler.py
grep "def run_housekeeping" orchestrator/scheduler.py
```

### 1.5 Spawn strategies

- [ ] `spawn_implementer(ctx)` — prepare_task_directory + invoke_claude
- [ ] `spawn_lightweight(ctx)` — no worktree, runs in parent project
- [ ] `spawn_worktree(ctx)` — general worktree-based agents
- [ ] `get_spawn_strategy(ctx)` — dispatch based on `spawn_mode` / `lightweight` in agent config
- [ ] No `if role == "implementer"` branches in `run_scheduler()`

**Verify:**
```bash
grep "^def spawn_" orchestrator/scheduler.py    # should list 3 strategies
grep "def get_spawn_strategy" orchestrator/scheduler.py
```

## 2. Agent Directories

### 2.1 Template structure in `packages/client/agents/`

- [ ] `packages/client/agents/implementer/` exists with:
  - `agent.yaml` (role, model, max_turns, interval, spawn_mode, allowed_tools)
  - `prompt.md` (system prompt template with `$variable` substitution)
  - `instructions.md` (implementation guidelines)
  - `scripts/` (submit-pr, run-tests, finish, fail, record-progress)
- [ ] `packages/client/agents/gatekeeper/` exists with same structure:
  - `agent.yaml`
  - `prompt.md`
  - `instructions.md`
  - `scripts/` (check-debug-code, check-scope, diff-stats, post-review, run-tests)

**Verify:**
```bash
find packages/client/agents -type f | sort
```

### 2.2 Scaffolded copies in `.octopoid/agents/`

- [ ] `octopoid init` copies templates to `.octopoid/agents/`
- [ ] Scaffolded copies are independently editable (user owns them)
- [ ] Custom agents (e.g. github-issue-monitor) live only in `.octopoid/agents/`

**Verify:** Run `octopoid init` in a temp directory — check `.octopoid/agents/` is populated.

### 2.3 Fleet config format in `.octopoid/agents.yaml`

- [ ] Uses `fleet:` key (not `agents:`)
- [ ] Each entry has `name:` and `type:` (referencing agent directory)
- [ ] Type defaults come from `agent.yaml` in the agent directory
- [ ] Fleet entries can override defaults (model, interval, etc.)
- [ ] Custom agents use `type: custom` with `path:` to their directory

**Verify:** Read `.octopoid/agents.yaml` — should look like:
```yaml
fleet:
  - name: implementer-1
    type: implementer
    enabled: true
  - name: github-issue-monitor
    type: custom
    path: .octopoid/agents/github-issue-monitor/
```

### 2.4 Config resolution in `get_agents()`

- [ ] `orchestrator/config.py:get_agents()` reads `fleet:` format
- [ ] Resolves agent directory: product templates → scaffolded copies → custom path
- [ ] Merges type defaults from `agent.yaml` with fleet overrides
- [ ] Each returned config includes `agent_dir` key
- [ ] No legacy `agents:` format support

**Verify:**
```python
from orchestrator.config import get_agents
agents = get_agents()
for a in agents:
    print(a.get('name'), a.get('agent_dir'), a.get('spawn_mode'))
```

### 2.5 Scripts and prompts come from agent directory

- [ ] `prepare_task_directory()` reads scripts from `agent_dir/scripts/`, no fallback
- [ ] `prepare_task_directory()` reads prompt from `agent_dir/prompt.md`, no fallback
- [ ] `prepare_task_directory()` reads instructions from `agent_dir/instructions.md`
- [ ] No references to `orchestrator/agent_scripts/` or `orchestrator/prompts/`

**Verify:**
```bash
grep -n "agent_scripts\|legacy\|fallback\|render_prompt" orchestrator/scheduler.py
# Should return nothing
```

## 3. Old Code Deleted

### 3.1 Directories removed

- [ ] `orchestrator/agent_scripts/` — gone
- [ ] `orchestrator/prompts/` — gone
- [ ] `commands/agent/` — gone
- [ ] `orchestrator/roles/` — all deleted except `github_issue_monitor.py` and `__init__.py`
- [ ] `packages/client/src/roles/` — gone (if no TS code imports it)

### 3.2 Legacy test files deleted

Tests that import from deleted modules must be removed:

- [ ] `tests/test_orchestrator_impl.py` (1349 lines) — tests `orchestrator.roles.orchestrator_impl`
- [ ] `tests/test_proposer_git.py` (342 lines) — tests `orchestrator.roles.proposer`
- [ ] `tests/test_compaction_hook.py` (263 lines) — imports `orchestrator.roles.base`
- [ ] `tests/test_tool_counter.py` (304 lines) — imports `orchestrator.roles.base` and `orchestrator.roles.orchestrator_impl`
- [ ] `tests/test_breakdown_context.py` (37 lines) — imports `orchestrator.roles.breakdown`
- [ ] `tests/test_pre_check.py` (6 lines) — tests `orchestrator.roles.pre_check`
- [ ] `tests/test_agent_env.py` (184 lines) — imports `orchestrator.roles.base`

Total: ~2485 lines of tests for deleted code.

**Verify:**
```bash
grep -rl "orchestrator\.roles" tests/ | grep -v __pycache__
# Should return nothing (or only github_issue_monitor tests if those exist)
```

### 3.3 Dead functions removed from scheduler.py

- [ ] `render_prompt()` — gone
- [ ] `get_role_constraints()` — gone
- [ ] `DEFAULT_AGENT_INSTRUCTIONS_TEMPLATE` — gone
- [ ] `setup_agent_commands()` — gone
- [ ] `generate_agent_instructions()` — gone

### 3.4 Line count — expect a large drop

The whole point of the refactor is to reduce complexity. If the line count doesn't drop significantly, the refactor kept complexity it was supposed to destroy.

**Files being deleted:**
| Category | Lines |
|----------|-------|
| `orchestrator/roles/*.py` (15 files) | ~4,348 |
| Legacy test files (7 files) | ~2,485 |
| `commands/agent/*.md` (11 files) | ~1,265 |
| `packages/client/src/roles/*.ts` (5 files) | ~1,242 |
| `orchestrator/agent_scripts/` (5 scripts) | ~344 |
| `orchestrator/prompts/implementer.md` | ~41 |
| Dead functions in scheduler.py | ~400 est. |
| **Total deleted** | **~10,125** |

**Current baselines:**
- `orchestrator/` Python: 16,887 lines → target ~12,100 (-28%)
- `tests/` Python: 11,318 lines → target ~8,800 (-22%)
- `orchestrator/scheduler.py`: 1,990 lines (pre-refactor) / 2,190 (current with refactor) → target ~1,600

**Acceptance criteria:**
- [ ] `orchestrator/scheduler.py` under 1,700 lines
- [ ] `orchestrator/` total under 13,000 lines
- [ ] `tests/` total under 9,500 lines
- [ ] Net project reduction of at least 8,000 lines

**Verify:**
```bash
wc -l orchestrator/scheduler.py
find orchestrator -name "*.py" -not -path "*__pycache__*" | xargs wc -l | tail -1
find tests -name "*.py" -not -path "*__pycache__*" | xargs wc -l | tail -1
```

## 4. No Behaviour Changes

The refactor should not change what the system does — same guards, same logic, same spawn behaviour.

### 4.1 Guards are equivalent

- [ ] Paused agents are skipped
- [ ] Running agents (live PID) are skipped; dead PIDs are cleaned up
- [ ] Interval is respected
- [ ] Backpressure blocks agents when queue limits are hit
- [ ] Pre-check runs before claiming
- [ ] Task claiming works for claimable roles, skipped for lightweight

### 4.2 Spawn is equivalent

- [ ] Implementers get: task directory with scripts, prompt, env.sh, then `claude -p`
- [ ] Lightweight agents run in parent project via `python -m`
- [ ] Worktree agents get worktree + commands + instructions + env

## 5. Integration Tests

### 5.1 Unit tests (existing)

- [ ] `tests/test_scheduler_refactor.py` covers guard functions, spawn strategies, evaluate_agent, run_housekeeping

### 5.2 End-to-end integration tests (new — needed)

These verify the full pipeline works, not just individual functions.

**Test: Scheduler tick with paused system**
```
1. Set `paused: true` in agents.yaml
2. Run `python3 -m orchestrator.scheduler --once --debug`
3. Verify: exits immediately, no agents spawned, debug log says "paused"
```

**Test: Scheduler tick spawns implementer**
```
1. Ensure system unpaused, agents configured
2. Create a task in incoming queue
3. Run scheduler tick
4. Verify: task moves to claimed, agent process is spawned
5. Verify: task directory exists with scripts/, prompt, env.sh, task.json
6. Verify: scripts came from agent directory (not orchestrator/agent_scripts/)
```

**Test: Guard chain blocks correctly**
```
1. Start an agent (leave it running)
2. Run scheduler tick
3. Verify: agent is blocked by guard_not_running, not spawned again
4. Kill the agent process
5. Run scheduler tick
6. Verify: agent state cleaned up, new agent spawned
```

**Test: Backpressure blocks spawn**
```
1. Set max_claimed: 1 in agents.yaml
2. Create and claim one task
3. Create another incoming task
4. Run scheduler tick
5. Verify: second agent blocked by backpressure, not spawned
```

**Test: Fleet config resolution**
```
1. Set fleet config with type: implementer
2. Verify get_agents() returns config with agent_dir pointing to implementer directory
3. Verify type defaults from agent.yaml are merged
4. Verify fleet overrides (e.g. model: opus) take precedence
```

**Test: Agent completes task end-to-end**
```
1. Create a trivial task (e.g. "add a comment to README")
2. Let scheduler spawn an implementer
3. Wait for agent to finish
4. Verify: task moves through claimed → provisional → done
5. Verify: PR was created
6. Verify: scripts from agent directory were used (check task dir)
```

## 6. Manual Verification Checklist

Run these after merging to confirm the system works:

### Quick smoke test (~2 min)
```bash
# 1. Check config loads
python3 -c "from orchestrator.config import get_agents; print(get_agents())"

# 2. Check scheduler runs clean
python3 -m orchestrator.scheduler --once --debug

# 3. Check no legacy references
grep -r "agent_scripts\|render_prompt\|legacy\|fallback" orchestrator/ --include="*.py" | grep -v __pycache__

# 4. Check line count
wc -l orchestrator/scheduler.py
```

### Full system test (~10 min)
```bash
# 1. Unpause system
# Edit .octopoid/agents.yaml: paused: false

# 2. Create a test task
python3 -c "
from orchestrator.queue_utils import get_sdk
sdk = get_sdk()
sdk.tasks.create(
    id='TEST-smoke-001',
    title='Smoke test: add comment to CHANGELOG',
    role='implement',
    priority='P2',
    queue='incoming',
    branch='feature/client-server-architecture',
    content='Add a comment at the top of CHANGELOG.md saying: # Smoke test - delete this line'
)
"

# 3. Wait for scheduler to pick it up (check every 30s)
watch -n 30 "python3 -c \"
from orchestrator.queue_utils import get_sdk
t = get_sdk().tasks.get('TEST-smoke-001')
print(f'Queue: {t[\"queue\"]}, Claimed by: {t.get(\"claimed_by\", \"-\")}')
\""

# 4. When claimed, verify task directory
ls .octopoid/runtime/tasks/TEST-smoke-001/
ls .octopoid/runtime/tasks/TEST-smoke-001/scripts/
cat .octopoid/runtime/tasks/TEST-smoke-001/prompt.md | head -20
# Scripts and prompt should reference agent directory content

# 5. When done, verify PR exists
gh pr list --search "TEST-smoke-001"

# 6. Clean up
python3 -c "
from orchestrator.queue_utils import get_sdk
get_sdk().tasks.update('TEST-smoke-001', queue='failed', failure_reason='smoke test cleanup')
"
```

### Regression test
```bash
# Run all unit tests
pytest tests/ -v

# Check the scheduler refactor tests specifically
pytest tests/test_scheduler_refactor.py -v
```
