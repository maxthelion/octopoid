# Octopoid v2.0

**Distributed AI orchestrator for software development** - Run multiple AI agents in parallel across machines to build software autonomously.

## What is Octopoid?

Octopoid is a **distributed task orchestration system** that uses Claude AI to automatically implement features, fix bugs, and manage software development workflows. Think of it as CI/CD, but for development itself.

**Key Features:**
- Multiple AI agents working in parallel (implementer, gatekeeper, breakdown)
- Distributed execution - run orchestrators on multiple machines (local, VMs, cloud)
- Task queue with priorities, dependencies, and lease-based state management
- Automated code review via gatekeeper agent (multi-round reviews)
- Declarative flow system - YAML-defined state machines for task transitions
- Agent pool model - configure max instances per blueprint, automatic PID tracking
- Pure function agents - agents write results, the scheduler handles the rest
- Task-specific git worktrees on detached HEAD - parallel execution without conflicts
- Textual TUI dashboard with kanban board, agent status, drafts, and task detail views
- Cloudflare Workers + D1 backend - serverless, globally distributed

## Architecture

### Deployment

```
+---------------------------------------------+
|        Cloudflare Workers Server             |
|  +----------------------------------------+ |
|  |  REST API (Hono framework)              | |
|  |  Tasks, Projects, Drafts, Orchestrators | |
|  |  Lease-based claiming, atomic state     | |
|  +----------------------------------------+ |
|  +----------------------------------------+ |
|  |  D1 Database (SQLite at the edge)       | |
|  +----------------------------------------+ |
+---------------------------------------------+
             ^              ^
             |              |
    +--------+-------+   +-+------------+
    | Orchestrator 1  |   | Orchestrator 2 |
    | (Laptop)        |   | (GPU VM)       |
    |                 |   |                |
    | - Scheduler     |   | - Scheduler    |
    | - Agent pool    |   | - Agent pool   |
    | - Worktrees     |   | - Worktrees    |
    | - Python SDK    |   | - Python SDK   |
    +-----------------+   +----------------+
```

### Internal Architecture

The system has four layers: **server** (state storage + API), **SDK** (Python client for the API), **scheduler** (orchestration logic), and **agents** (Claude Code instances that do the work).

#### Pure Function Agents

Agents are **pure functions**. An agent receives a task, does work in a git worktree, and writes a `result.json` file. That's it. The agent never pushes branches, creates PRs, updates task state, or calls the server API. All side effects are handled by the **scheduler** after the agent exits, using flow-defined steps.

```
Agent writes:   { "outcome": "done" }
                or { "outcome": "failed", "reason": "..." }

Scheduler runs: push_branch -> run_tests -> create_pr
```

This separation means agents are stateless and testable. The scheduler controls all state transitions.

#### Pool Model

Agents are configured as **blueprints** in `.octopoid/agents.yaml`. Each blueprint defines a role, model, and `max_instances` (how many concurrent copies can run). The scheduler tracks running instances via PID files (`running_pids.json` per blueprint) and only spawns new instances when capacity is available.

```yaml
agents:
  implementer:
    role: implementer
    max_instances: 2         # up to 2 concurrent implementers
    interval_seconds: 60
    max_turns: 150
    model: sonnet

  sanity-check-gatekeeper:
    role: gatekeeper
    spawn_mode: scripts
    claim_from: provisional  # reviews, not implements
    interval_seconds: 120
    max_turns: 50
    model: sonnet
    agent_dir: .octopoid/agents/gatekeeper
    max_instances: 1
```

Instance naming is automatic: `implementer-1`, `implementer-2`, etc. Dead PIDs are detected via `os.kill(pid, 0)` and cleaned up when results are processed.

#### Flows (Declarative State Machines)

Flows define how tasks move through the system. They're YAML files in `.octopoid/flows/` that specify transitions, conditions, and post-transition steps.

```yaml
# .octopoid/flows/default.yaml
name: default
description: Standard implementation with review

transitions:
  "incoming -> claimed":
    agent: implementer

  "claimed -> provisional":
    runs: [push_branch, run_tests, create_pr]

  "provisional -> done":
    conditions:
      - name: gatekeeper_review
        type: agent
        agent: gatekeeper
        on_fail: incoming       # reject sends task back to incoming
    runs: [post_review_comment, merge_pr]
```

Steps are registered in `orchestrator/steps.py` via `@register_step("name")`. Adding a new agent type means creating a flow YAML and registering steps -- no scheduler code changes needed.

See [docs/flows.md](docs/flows.md) for full documentation.

#### Scheduler Pipeline

The scheduler runs as a single tick per invocation (triggered by launchd every 10 seconds). Each tick:

1. **Housekeeping** -- process finished agents, requeue expired leases, run hooks on provisional tasks
2. **Poll** -- one `GET /api/v1/scheduler/poll` call fetches queue counts, provisional tasks, and registration status
3. **Evaluate each blueprint** through a guard chain (cheapest checks first):

| Guard | What it checks |
|-------|----------------|
| `guard_enabled` | Blueprint not paused |
| `guard_pool_capacity` | `running_instances < max_instances` |
| `guard_interval` | Enough time since last spawn |
| `guard_backpressure` | Queue has claimable tasks |
| `guard_claim_task` | Atomically claims a task from server |

If all guards pass, the scheduler spawns an agent instance.

#### Branching Model

Worktrees are **always created on detached HEAD** -- never on a named branch. This prevents git from refusing to check out a branch that's already checked out in another worktree (critical when running multiple agents in parallel).

The named branch (`agent/<task-id>`) is only created at push time, by the `push_branch` step. Branch mismatch detection ensures worktrees are recreated if the base branch changes.

`repo.base_branch` in `.octopoid/config.yaml` controls which branch all tasks branch from (e.g. `main` or a feature branch).

#### Agent Directory Structure

Each agent type has a directory under `.octopoid/agents/` containing everything needed to run it:

```
.octopoid/agents/implementer/
  agent.yaml           # role, model, spawn_mode, allowed_tools, max_turns
  prompt.md            # prompt template ($task_id, $task_content, etc.)
  instructions.md      # supplementary instructions appended to prompt
  scripts/
    run-tests          # test runner wrapper
    record-progress    # save progress notes

.octopoid/agents/gatekeeper/
  agent.yaml           # role, model (no Write/Edit in allowed_tools)
  prompt.md            # review prompt template
  instructions.md      # review criteria, rejection format
  scripts/
    run-tests          # verify tests on PR branch
    check-scope        # flag unexpected file changes (advisory)
    check-debug-code   # find leftover debug code (advisory)
    diff-stats         # diff statistics
```

The `prompt.md` uses `string.Template` substitution (`$task_id`, `$task_content`, `$global_instructions`, etc.) and is rendered by the scheduler before spawning.

## Installation

### Server (One-Time Setup)

The server lives in its own repo: **[octopoid-server](https://github.com/maxthelion/octopoid-server)**

**Option A: One-click deploy** -- Use the Deploy button in the octopoid-server repo.

**Option B: Manual deploy:**

```bash
git clone https://github.com/maxthelion/octopoid-server.git
cd octopoid-server
npm install

# Create D1 database and copy database_id to wrangler.toml
npx wrangler d1 create octopoid-db

# Apply migrations and deploy
npx wrangler d1 migrations apply octopoid-db --remote
npx wrangler deploy

# Server URL: https://octopoid-server.your-username.workers.dev
```

**For development** the server is included as a git submodule at `submodules/server/`:

```bash
git clone --recurse-submodules https://github.com/maxthelion/octopoid.git
# Or if already cloned:
git submodule update --init
```

### Client (Each Machine)

**Note:** The npm package is not yet published. Install from source for now.

#### Install from Source (Current Method)

```bash
# Clone repository
git clone https://github.com/maxthelion/octopoid.git
cd octopoid

# Install dependencies (requires pnpm)
pnpm install

# Build all packages
pnpm build

# Link client globally
cd packages/client
sudo npm link

# Verify installation
octopoid --version
```

#### Install from npm (Coming Soon)

```bash
# Not yet published - use source install above
npm install -g octopoid
```

## Setup

### Initialize Octopoid in Your Project

```bash
# Navigate to your project
cd ~/my-project

# Initialize Octopoid (creates .octopoid/ directory)
octopoid init --server https://octopoid-server.your-username.workers.dev --cluster prod

# This creates:
# .octopoid/
# +-- config.yaml      # Server connection, cluster settings
# +-- agents.yaml      # Agent pool configuration (blueprints)
# +-- agents/           # Agent type directories (prompt, scripts, config)
# +-- flows/            # Flow definitions (state machines)
# +-- runtime/          # PIDs, locks, orchestrator ID (don't commit)
# +-- logs/             # Scheduler, agent, and task logs (don't commit)
```

### Configuration Files

#### `.octopoid/config.yaml`

```yaml
# Server connection
server:
  enabled: true
  url: https://octopoid-server.your-username.workers.dev
  cluster: prod
  machine_id: laptop-001

# Repository settings
repo:
  path: /path/to/your/project
  url: https://github.com/your-org/your-repo.git  # used for orchestrator registration
  base_branch: main  # all task branches fork from here

# Hooks -- lifecycle actions run during task processing
hooks:
  before_submit:
    - rebase_on_main
    - create_pr
```

#### `.octopoid/agents.yaml`

```yaml
agents:
  implementer:                 # blueprint name
    role: implementer          # agent role (resolves agent directory)
    max_instances: 2           # pool: up to 2 concurrent agents
    interval_seconds: 60       # minimum seconds between spawns
    max_turns: 150             # Claude Code turn limit per task
    model: sonnet              # Claude model to use

  sanity-check-gatekeeper:
    role: gatekeeper
    spawn_mode: scripts
    claim_from: provisional    # claims from provisional queue, not incoming
    interval_seconds: 120
    max_turns: 100
    model: sonnet
    agent_dir: .octopoid/agents/gatekeeper
    max_instances: 1

  github-issue-monitor:
    role: custom
    path: .octopoid/agents/github-issue-monitor/
    interval_seconds: 900
    lightweight: true          # no worktree, runs in parent project
    max_instances: 1
    paused: true               # disabled by default
```

### Install Slash Commands

Octopoid ships management skills (slash commands) for Claude Code. Install them to your project's `.claude/commands/`:

```bash
octopoid install-commands
```

This installs commands like `/enqueue`, `/queue-status`, `/agent-status`, `/draft-idea`, etc. Run it again after upgrading Octopoid to pick up new or updated commands.

### Dashboard

`octopoid init` installs an `octopoid-dash` wrapper script in your project root. Run it to launch the Textual TUI:

```bash
./octopoid-dash
```

Requires Python dependencies (`textual`, `httpx`, etc.). The wrapper sets `PYTHONPATH` to the octopoid submodule so imports resolve correctly.

### Set API Key

```bash
# Required for agent execution
export ANTHROPIC_API_KEY="sk-ant-..."

# Add to your shell profile for persistence
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
```

## What Files Does Octopoid Create?

### `.octopoid/` - Main Directory

```
.octopoid/
+-- config.yaml          # Server URL, cluster name, base branch
+-- agents.yaml          # Agent pool blueprints (dict format)
+-- agents/              # Agent type directories
|   +-- implementer/     # Implementer agent
|   |   +-- agent.yaml   # Role, model, spawn_mode, allowed_tools
|   |   +-- prompt.md    # Prompt template (Template substitution)
|   |   +-- instructions.md  # Supplementary instructions
|   |   +-- scripts/     # Helper scripts (run-tests, record-progress)
|   +-- gatekeeper/      # Gatekeeper (review) agent
|       +-- agent.yaml
|       +-- prompt.md
|       +-- instructions.md
|       +-- scripts/     # run-tests, check-scope, check-debug-code
+-- flows/               # Declarative state machines
|   +-- default.yaml     # incoming -> claimed -> provisional -> done
+-- tasks/               # Task description files (TASK-{id}.md)
+-- runtime/             # Runtime state (don't commit)
|   +-- orchestrator_id.txt
|   +-- agents/          # Per-blueprint PID tracking
|   |   +-- implementer/
|   |   |   +-- running_pids.json   # {pid: {task_id, instance_name}}
|   |   |   +-- state.json
|   |   +-- gatekeeper/
|   |       +-- running_pids.json
|   |       +-- state.json
|   +-- tasks/           # Per-task runtime directories
|       +-- TASK-abc123/
|           +-- worktree/    # Git worktree (detached HEAD)
|           +-- task.json    # Task metadata
|           +-- prompt.md    # Rendered prompt
|           +-- env.sh       # Environment variables
|           +-- scripts/     # Copied from agent directory
|           +-- result.json  # Written by agent, read by scheduler
|           +-- stdout.log   # Agent output
|           +-- stderr.log
+-- logs/                # All logs (don't commit)
    +-- scheduler-YYYY-MM-DD.log
    +-- dashboard.log
    +-- tasks/
        +-- TASK-abc123.log  # Lifecycle events (CREATED, CLAIMED, etc.)
```

### `.gitignore` Updates

Add these to your `.gitignore`:

```gitignore
# Octopoid runtime files
.octopoid/runtime/
.octopoid/logs/
```

## Usage

### Start the Orchestrator

```bash
# Start orchestrator (runs continuously)
octopoid start

# Or run in background
octopoid start --daemon

# Check status
octopoid status

# Stop orchestrator
octopoid stop
```

### Running with launchd or cron

The scheduler runs a single tick per invocation (evaluate agents, spawn any that are due, exit), so it's designed to be triggered by an external timer. A file-based lock (`scheduler.lock`) prevents overlapping runs.

#### macOS launchd (recommended)

1. Copy the template plist and fill in placeholders:

```bash
cp orchestrator/com.octopoid.scheduler.plist ~/Library/LaunchAgents/com.octopoid.scheduler.plist
```

2. Edit `~/Library/LaunchAgents/com.octopoid.scheduler.plist` -- replace every `/path/to/your/project` with your actual project path and set your `ANTHROPIC_API_KEY`.

3. Load the agent:

```bash
launchctl load ~/Library/LaunchAgents/com.octopoid.scheduler.plist
```

4. To stop:

```bash
launchctl unload ~/Library/LaunchAgents/com.octopoid.scheduler.plist
```

#### cron (Linux / macOS)

Add a one-liner to your crontab (`crontab -e`):

```cron
* * * * * cd /path/to/your/project && ANTHROPIC_API_KEY="sk-ant-..." orchestrator/venv/bin/orchestrator-scheduler --debug
```

This fires every 60 seconds. The scheduler lock ensures that if a tick takes longer than a minute, the next invocation exits immediately rather than overlapping.

#### Pausing / Resuming

Set `paused: true` at the top level of `.octopoid/agents.yaml` to pause the entire system. Individual blueprints can be paused with their own `paused: true` flag.

Running agents are not killed when you pause -- they finish their current task. Pausing just prevents new agents from being spawned.

### CLI Commands

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `octopoid tasks` | List tasks | `--queue` / `-q` filter by queue |
| `octopoid task <id>` | Show task detail | `--verbose` / `-v` show all fields |
| `octopoid requeue <id>` | Move a claimed/failed task back to incoming | |
| `octopoid cancel <id>` | Delete a task | `--force` / `-f` skip confirmation |
| `octopoid worktrees` | List task worktrees | |
| `octopoid worktrees-clean` | Prune stale task worktrees | `--dry-run` preview only |
| `octopoid install-commands` | Install/update slash commands to `.claude/commands/` | `--force` / `-f` overwrite all |

```bash
# Create a task
octopoid enqueue "Add user authentication" \
  --role implement \
  --priority P1

# List incoming tasks
octopoid tasks --queue incoming

# Inspect a specific task
octopoid task TASK-2a4ad137 --verbose

# Requeue a failed task for retry
octopoid requeue TASK-3b950eb4

# Cancel a stuck or unwanted task
octopoid cancel TASK-fae4ad46

# Clean up orphaned worktrees
octopoid worktrees-clean --dry-run
```

### Dashboard

A Textual TUI for monitoring the system. Polls the server every 5 seconds via the Python SDK.

```bash
./octopoid-dash
```

**Tabs:**

| Tab | Key | Contents |
|-----|-----|----------|
| Work | W | Kanban board: Incoming / In Progress / In Review columns with task cards showing agent, claim duration, and turn progress bar |
| Inbox | I | Proposals, messages, and draft summaries |
| Agents | A | Master-detail: agent list with status badges, detail pane with role, current task, recent work |
| Tasks | T | DataTable of completed/failed/recycled tasks (last 7 days) with Done/Failed/Proposed sub-tabs |
| Drafts | F | Server-backed draft list with status filter buttons and markdown content preview |

Click a task card or press Enter to open a detail modal with Diff, Description, Result, and Logs tabs.

### Manage Drafts

```bash
# Create a draft (idea/proposal)
octopoid draft create "Add dark mode support" \
  --author "Your Name" \
  --status idea

# List drafts
octopoid draft list --status idea

# Update draft status
octopoid draft update dark-mode-support --status approved
```

### Manage Projects

```bash
# Create a project (multi-task container)
octopoid project create "User Dashboard Redesign" \
  --description "Complete redesign of user dashboard" \
  --status active

# Show project with all tasks
octopoid project show user-dashboard-redesign

# Update project
octopoid project update user-dashboard-redesign --status completed
```

## How It Works

### Task Lifecycle

```
incoming -> claimed -> provisional -> done
              |                        ^
              v                        |
           (agent works)          (gatekeeper approves)
              |                        |
              v                        |
           result.json            merge PR
              |
              v
     scheduler runs steps:
     push_branch -> run_tests -> create_pr
                                      |
                                      v
                                provisional
                                                    |
                                                    v
                                          (gatekeeper reviews)
                                             /            \
                                          approve        reject
                                            |              |
                                            v              v
                                          done          incoming (retry)
```

### Step by Step

1. **Task Creation**: Create tasks via `octopoid enqueue` or the Python SDK. Task description is written to `.octopoid/tasks/TASK-{id}.md` and registered on the server.

2. **Scheduler Tick**: Every 10 seconds, the scheduler runs housekeeping (process finished agents, requeue expired leases) and evaluates each blueprint through the guard chain.

3. **Guard Chain**: Each blueprint is checked: is it enabled? Is there pool capacity? Has enough time passed? Are there tasks to claim? If all guards pass, the scheduler atomically claims a task from the server (lease-based, prevents double-claiming across machines).

4. **Agent Spawning**: The scheduler creates a task runtime directory with a git worktree (detached HEAD from `base_branch`), renders the prompt template, copies scripts, and launches `claude` as a subprocess.

5. **Agent Execution**: The agent (Claude Code) works in its isolated worktree. It reads the task description, implements changes, commits code, and writes `result.json` with `{"outcome": "done"}`. The agent has no access to the server API and cannot push branches or create PRs.

6. **Result Processing**: When the agent process exits, the scheduler reads `result.json` and executes the flow-defined steps for the `claimed -> provisional` transition: `push_branch`, `run_tests`, `create_pr`. The flow engine then performs the state transition automatically.

7. **Gatekeeper Review**: A gatekeeper blueprint claims `provisional` tasks, spawns a Claude Code instance with read-only tools, reviews the diff, and writes `result.json` with `{"decision": "approve"}` or `{"decision": "reject", "comment": "..."}`.

8. **Accept or Reject**: On approval, the scheduler posts a review comment, merges the PR, and moves the task to `done`. On rejection, it posts rejection feedback to the PR and moves the task back to `incoming` for the implementer to retry (up to 3 rounds).

### Python SDK

The `OctopoidSDK` (`packages/python-sdk/`) is the Python client for the server API. All orchestrator-server communication goes through it.

```python
from octopoid_sdk import OctopoidSDK

sdk = OctopoidSDK(server_url="https://...", api_key="...")

# Tasks
tasks = sdk.tasks.list(queue="incoming")
task = sdk.tasks.claim(orchestrator_id, agent_name="impl-1")
sdk.tasks.submit(task_id, commits_count=3, turns_used=45)

# Drafts
drafts = sdk.drafts.list(status="idea")
sdk.drafts.create(title="Dark mode", author="human", status="idea")

# Projects
projects = sdk.projects.list()
sdk.projects.create(title="Dashboard Redesign", status="active")

# Scheduler batch endpoint (replaces ~14 individual API calls per tick)
poll_data = sdk.poll(orchestrator_id)
# Returns: queue_counts, provisional_tasks, orchestrator_registered
```

## Multi-Machine Setup

Run orchestrators on multiple machines pointing at the same server:

```bash
# Machine 1 (Laptop)
octopoid init --server https://... --cluster prod --machine-id laptop-001
octopoid start --daemon

# Machine 2 (GPU VM)
octopoid init --server https://... --cluster prod --machine-id vm-gpu-001
octopoid start --daemon

# Machine 3 (Linux Workstation)
octopoid init --server https://... --cluster prod --machine-id workstation-001
octopoid start --daemon
```

All orchestrators coordinate via the server. Task claiming is atomic and lease-based -- no double-claiming, no conflicts.

## Troubleshooting

### "ANTHROPIC_API_KEY not found"

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### "Server unreachable"

```bash
# Check server status
curl https://octopoid-server.your-username.workers.dev/api/health
```

### "Worktree already exists"

```bash
# Clean up stale worktrees
octopoid worktrees-clean
git worktree prune
```

### `octopoid: command not found` after npm link

Ensure npm's global bin directory is in your PATH:

```bash
npm config get prefix
export PATH="$(npm config get prefix)/bin:$PATH"
source ~/.zshrc
```

### Dashboard shows no tasks

1. Verify server: `curl http://localhost:8787/api/health`
2. Check tasks exist: `octopoid tasks --queue incoming`
3. Clear Python cache: `find packages/dashboard -name '__pycache__' -type d -exec rm -rf {} +`

### Build errors with TypeScript

```bash
pnpm clean
rm -rf node_modules packages/*/node_modules
pnpm install
pnpm build
```

## License

MIT License

## Links

- **GitHub**: https://github.com/maxthelion/octopoid
- **Issues**: https://github.com/maxthelion/octopoid/issues
- **Flow system docs**: [docs/flows.md](docs/flows.md)
