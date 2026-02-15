# Octopoid v2.0

**Distributed AI orchestrator for software development** - Run multiple AI agents in parallel across machines to build software autonomously.

## What is Octopoid?

Octopoid is a **distributed task orchestration system** that uses Claude AI to automatically implement features, fix bugs, and manage software development workflows. Think of it as CI/CD, but for development itself.

**Key Features:**
- 🤖 **Multiple AI agents** working in parallel (implementer, gatekeeper, breakdown)
- 🌐 **Distributed execution** - Run orchestrators on multiple machines (local, VMs, cloud)
- 📋 **Task queue system** with priorities, dependencies, and state management
- 🔄 **Automated code review** via gatekeeper agent (multi-round reviews)
- 📝 **Drafts & Projects** - Organize ideas and multi-task features
- 🌳 **Task-specific worktrees** - Parallel execution without conflicts
- 📊 **Turn tracking & logging** - Per-task and per-agent logs
- ☁️ **Cloudflare Workers** backend - Serverless, globally distributed

## Architecture

```
┌─────────────────────────────────────────────┐
│        Cloudflare Workers Server            │
│  ┌────────────────────────────────────────┐ │
│  │  REST API (Hono framework)             │ │
│  │  - Tasks, Projects, Drafts, Orchestrators │
│  │  - State machine with lease management │ │
│  └────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────┐ │
│  │  D1 Database (SQLite at the edge)      │ │
│  └────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
             ▲              ▲
             │              │
    ┌────────┴───────┐   ┌─┴──────────┐
    │ Orchestrator 1  │   │ Orchestrator 2 │
    │ (Laptop)        │   │ (GPU VM)       │
    │                 │   │                │
    │ - Scheduler     │   │ - Scheduler    │
    │ - Agents        │   │ - Agents       │
    │ - Worktrees     │   │ - Worktrees    │
    │ - Local tasks   │   │ - Local tasks  │
    └─────────────────┘   └────────────────┘
```

## Installation

### Server (One-Time Setup)

The server lives in its own repo: **[octopoid-server](https://github.com/maxthelion/octopoid-server)**

**Option A: One-click deploy** — Use the Deploy button in the octopoid-server repo.

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
# ├── config.yaml      # Server connection, cluster settings
# ├── agents.yaml      # Agent configurations
# ├── runtime/         # PIDs, locks, orchestrator ID
# ├── logs/            # Scheduler, agent, and task logs
# └── worktrees/       # Git worktrees (one per task)
```

### Configuration Files

#### `.octopoid/config.yaml`

```yaml
# Server connection (remote mode)
server:
  enabled: true
  url: https://octopoid-server.your-username.workers.dev
  cluster: prod
  machine_id: laptop-001

# Repository settings
repo:
  path: /path/to/your/project
  base_branch: main

# Hooks — lifecycle actions run during task processing
# Default: before_submit: [create_pr]
# Built-in hooks: rebase_on_main, create_pr, run_tests
hooks:
  before_submit:
    - rebase_on_main
    - create_pr

# Task type definitions — override hooks per type
# task_types:
#   product:
#     hooks:
#       before_submit: [rebase_on_main, run_tests, create_pr]
#   hotfix:
#     hooks:
#       before_submit: [run_tests, create_pr]
```

#### `.octopoid/agents.yaml`

```yaml
agents:
  - name: implementer-1
    role: implement
    model: claude-sonnet-4-20250514
    max_turns: 50
    interval_seconds: 300
    paused: false

  - name: gatekeeper-1
    role: review
    model: claude-opus-4-20250514  # Use Opus for reviews
    max_turns: 20
    interval_seconds: 600
    paused: false

  - name: breakdown-1
    role: breakdown
    model: claude-sonnet-4-20250514
    max_turns: 30
    interval_seconds: 900
    paused: false
```

### Set API Key

```bash
# Required for agent execution
export ANTHROPIC_API_KEY="sk-ant-..."

# Add to your shell profile for persistence
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
```

## What Files Does Octopoid Create?

When you run `octopoid init`, these files and directories are created:

### `.octopoid/` - Main Directory

```
.octopoid/
├── config.yaml          # Server URL, cluster name, machine ID
├── agents.yaml          # Agent definitions (what roles run)
├── runtime/             # Runtime state (don't commit)
│   ├── orchestrator_id.txt    # Your registered orchestrator ID
│   ├── orchestrator.pid       # Process ID when running
│   └── agents/                # Per-agent state
│       ├── implementer-1/
│       │   └── state.json     # Running, last finished, etc.
│       └── gatekeeper-1/
│           └── state.json
├── logs/                # All logs (don't commit)
│   ├── scheduler-2026-02-11.log    # Scheduler activity
│   ├── agents/                      # Per-agent logs
│   │   ├── implementer-1-2026-02-11.log
│   │   └── gatekeeper-1-2026-02-11.log
│   └── tasks/                       # Per-task logs (aggregated)
│       ├── task-123.log
│       └── task-456.log
└── worktrees/           # Git worktrees (one per task)
    ├── task-123/        # Isolated working directory for task-123
    │   └── .git         # Worktree git metadata
    └── task-456/        # Isolated working directory for task-456
        └── .git
```

### `.gitignore` Updates

Add these to your `.gitignore`:

```gitignore
# Octopoid runtime files
.octopoid/runtime/
.octopoid/logs/
.octopoid/worktrees/
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

2. Edit `~/Library/LaunchAgents/com.octopoid.scheduler.plist` — replace every `/path/to/your/project` with your actual project path and set your `ANTHROPIC_API_KEY`.

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

The scheduler checks for a `PAUSE` file on each tick. No need to unload launchd or stop cron.

```bash
# Pause (scheduler skips all ticks while paused)
./scripts/pause.sh on

# Resume
./scripts/pause.sh off

# Toggle
./scripts/pause.sh

# Check current state
./scripts/pause.sh status
```

Running agents are not killed when you pause — they finish their current task. Pausing just prevents new agents from being spawned.

### CLI Commands

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `octopoid tasks` | List tasks | `--queue` / `-q` filter by queue |
| `octopoid task <id>` | Show task detail | `--verbose` / `-v` show all fields |
| `octopoid requeue <id>` | Move a claimed/failed task back to incoming | |
| `octopoid cancel <id>` | Delete a task | `--force` / `-f` skip confirmation |
| `octopoid worktrees` | List task worktrees | |
| `octopoid worktrees-clean` | Prune stale task worktrees | `--dry-run` preview only |

```bash
# Create a task
octopoid enqueue "Add user authentication" \
  --role implement \
  --priority P1 \
  --complexity M

# List incoming tasks
octopoid tasks --queue incoming

# Inspect a specific task
octopoid task gh-8-2a4ad137 --verbose

# Requeue a failed task for retry
octopoid requeue gh-7-3b950eb4

# Cancel a stuck or unwanted task
octopoid cancel fae4ad46

# Clean up orphaned worktrees
octopoid worktrees-clean --dry-run
```

### Dashboard

A terminal UI for monitoring tasks, agents, PRs, and queue state. Reads the server URL from `.octopoid/config.yaml` automatically.

```bash
# Launch dashboard (reads server from config.yaml)
python octopoid-dash.py

# Or override the server URL
python octopoid-dash.py --server http://localhost:8787

# Demo mode (no server needed)
python octopoid-dash.py --demo
```

### Manage Drafts

```bash
# Create a draft (idea/proposal)
octopoid draft create "Add dark mode support" \
  --author "Your Name" \
  --status idea \
  --domain frontend

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
  --status active \
  --auto-accept  # Skip gatekeeper for all project tasks

# Show project with all tasks
octopoid project show user-dashboard-redesign

# Update project
octopoid project update user-dashboard-redesign --status completed
```

### Project Branch Sequencing

Projects group sequential tasks that build on each other's work. When you set `--branch` and `--auto-accept` on a project, Octopoid manages a shared feature branch that accumulates all task commits.

**How it works:**

1. **Lazy Branch Creation**: When the first task in a project is claimed, the scheduler automatically creates the project branch from the base branch and pushes it to origin.

2. **Branch Inheritance**: All tasks created with a `project_id` automatically inherit the project's branch. You don't need to specify the branch for each task.

3. **Sequential Accumulation**: Each task's worktree is created from the latest state of the project branch, so task 2 sees task 1's commits, task 3 sees 1+2, and so on.

4. **Direct Merge (No Per-Task PRs)**: When `auto_accept: true` is set on a project, tasks merge their commits directly to the project branch instead of creating individual PRs. Each task auto-accepts after submission.

5. **Final PR**: When all tasks in the project are complete, the scheduler creates a single PR from the project branch to the base branch, showing the full accumulated diff.

**Example workflow:**

```bash
# Create a project with branch sequencing
octopoid project create "Feature Refactor" \
  --branch feature/refactor \
  --base feature/client-server \
  --auto-accept

# Create tasks (they inherit the project branch automatically)
octopoid enqueue "Refactor module A" --project feature-refactor
octopoid enqueue "Refactor module B" --project feature-refactor
octopoid enqueue "Refactor module C" --project feature-refactor

# Octopoid will:
# 1. Create feature/refactor branch (lazily, on first task claim)
# 2. Task 1: work on feature/refactor, merge commits, auto-accept
# 3. Task 2: start from feature/refactor with Task 1's work, merge commits, auto-accept
# 4. Task 3: start from feature/refactor with Task 1+2's work, merge commits, auto-accept
# 5. Create final PR: feature/refactor → feature/client-server

# View project status
octopoid project show feature-refactor
```

**When to use project branches:**

- ✅ Sequential refactors where later tasks need earlier changes
- ✅ Multi-phase feature implementations with dependencies
- ✅ Breaking a large change into smaller, ordered tasks
- ❌ Independent tasks that can work in parallel (use separate tasks without a project)

## How It Works

1. **Task Creation**: Create tasks via `octopoid enqueue` or manually edit `.md` files
2. **Server Sync**: Client syncs tasks to Cloudflare Workers server
3. **Orchestrator Registration**: Each client registers with the server (cluster + machine ID)
4. **Scheduler Loop**: Every 60 seconds, scheduler evaluates which agents should run
5. **Agent Spawning**: Agents claim tasks from server (atomic, lease-based)
6. **Task Execution**:
   - Agent creates task-specific worktree (`.octopoid/worktrees/{task_id}/`)
   - Agent creates feature branch (`agent/{task_id}-timestamp`)
   - Agent invokes Claude Code to implement the task
   - Agent commits changes
   - **Before-submit hooks** run in order (e.g. `rebase_on_main`, `run_tests`, `create_pr`)
   - If a hook fails with a remediation prompt, Claude attempts to fix and retries
   - Agent submits completion to server (moves to `provisional` queue)
7. **Gatekeeper Review**: Gatekeeper agent claims `provisional` tasks, reviews changes
   - **Accept**: Task moves to `done` queue, PR can be merged
   - **Reject**: Task returns to `incoming` queue with feedback (up to 3 rounds)
8. **Cleanup**: Worktree removed after task completion

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

**Result**: All three orchestrators coordinate via the server. Each claims different tasks. No conflicts, no double-claiming.

## Troubleshooting

### "ANTHROPIC_API_KEY not found"

```bash
# Set API key
export ANTHROPIC_API_KEY="sk-ant-..."
```

### "Server unreachable"

```bash
# Check server status
curl https://octopoid-server.your-username.workers.dev/api/health

# Test with local mode
octopoid init --local
octopoid start
```

### "Worktree already exists"

```bash
# Clean up stale worktrees
rm -rf .octopoid/worktrees/task-123
git worktree prune
```

## License

MIT License

## Links

- **GitHub**: https://github.com/maxthelion/octopoid
- **Issues**: https://github.com/maxthelion/octopoid/issues
- **Documentation**: [REQUIREMENTS_ANALYSIS.md](./REQUIREMENTS_ANALYSIS.md)

## Troubleshooting

### `octopoid: command not found` after npm link

Ensure npm's global bin directory is in your PATH:

```bash
# Check npm global bin location
npm config get prefix

# Add to PATH (add to ~/.bashrc or ~/.zshrc)
export PATH="$(npm config get prefix)/bin:$PATH"

# Reload shell
source ~/.bashrc  # or source ~/.zshrc
```

### `permission denied` during npm link

Use sudo for npm link:

```bash
cd packages/client
sudo npm link
```

### Dashboard shows no tasks (API mode)

If running dashboard with `--server` flag but seeing empty queues:

1. Verify server connection:
   ```bash
   curl http://localhost:8787/api/health
   ```

2. Check tasks exist on server:
   ```bash
   octopoid list --queue incoming
   ```

3. Ensure dashboard has latest code:
   ```bash
   git pull origin feature/client-server-architecture
   ```

### Build errors with TypeScript

Clear build cache and rebuild:

```bash
# Clean all packages
pnpm clean

# Reinstall dependencies
rm -rf node_modules packages/*/node_modules
pnpm install

# Rebuild
pnpm build
```

