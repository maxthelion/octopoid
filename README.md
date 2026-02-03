# Local Multi-Agent Scheduler

A file-driven orchestrator that runs on 1-minute ticks, evaluates configured agents, and runs them when overdue. Designed as a self-contained git submodule.

## Overview

This orchestrator manages multiple autonomous Claude Code agents that work on tasks in parallel. It supports two models:

### Task Model (v1)
The PM explores the codebase and creates tasks directly.

```
PM explores codebase → Creates tasks → Executors implement
```

### Proposal Model (v2)
Specialized proposers suggest work, a curator promotes the best proposals to tasks.

```
Proposers (specialists) → Proposals → Curator → Tasks → Executors
```

## Agent Roles

**Task Model:**
- **Product Manager** - Analyzes codebase, creates tasks directly

**Proposal Model:**
- **Proposers** - Specialists who propose work in their focus area
- **Curator** - Evaluates proposals, promotes to tasks, rejects with feedback

**Execution Layer (both models):**
- **Implementer** - Claims tasks, implements features, creates PRs
- **Tester** - Runs tests and adds test coverage
- **Reviewer** - Reviews code for bugs and security issues

## Quick Start

```bash
cd your-project
git submodule add https://github.com/maxthelion/octopoid.git orchestrator
```

Then ask Claude to read the orchestrator README and help you set it up.

## Configuration

### Claude Instructions

The orchestrator uses your project's existing `claude.md` file. Add these lines to it:

```markdown
If .agent-instructions.md exists in this directory, read and follow those instructions.

Check .orchestrator/messages/ for any agent messages and inform the user of warnings or questions.
```

When agents run, the scheduler generates a `.agent-instructions.md` file in each agent's worktree containing:
- Agent identity and role
- Current task details
- Role-specific constraints

This file is gitignored and regenerated each run. Your existing `claude.md` project instructions apply to all agents automatically.

### agents.yaml

```yaml
# Queue limits for backpressure control
queue_limits:
  max_incoming: 20
  max_claimed: 5
  max_open_prs: 10

# Agent definitions
agents:
  - name: pm-agent
    role: product_manager
    interval_seconds: 600

  - name: impl-agent-1
    role: implementer
    interval_seconds: 180

  - name: test-agent
    role: tester
    interval_seconds: 120

  - name: review-agent
    role: reviewer
    interval_seconds: 300
```

### global-instructions.md (Optional)

If you need agent-specific instructions beyond your `claude.md`, you can add them to `.orchestrator/global-instructions.md`. Most projects won't need this.

## Proposal Model (v2)

The proposal model separates concerns into three layers:

### 1. Proposal Layer - Specialists Propose Work

Proposers are specialized agents with a specific focus area:

| Proposer | Focus | Typical Proposals |
|----------|-------|-------------------|
| test-checker | Test quality | Fix flaky tests, add coverage |
| architect | Code structure | Refactoring, simplification |
| app-designer | Features | New functionality, UX |
| plan-reader | Project plans | Tasks from documented plans |

Configure proposers in `agents.yaml`:

```yaml
- name: test-checker
  role: proposer
  focus: test_quality
  interval_seconds: 86400  # Daily
```

Each proposer has independent backpressure:

```yaml
proposal_limits:
  test-checker:
    max_active: 5
    max_per_run: 2
```

### 2. Curation Layer - PM Evaluates Proposals

The curator (PM) does NOT explore the codebase directly. Instead:

- **Scores** proposals based on configurable weights
- **Promotes** good proposals to the task queue
- **Rejects** proposals with feedback (so proposers can learn)
- **Defers** proposals that aren't right for now
- **Escalates** conflicts to the project owner

Voice weights control proposer trust levels:

```yaml
voice_weights:
  plan-reader: 1.5    # Executing plans is priority
  architect: 1.2      # Simplification multiplies velocity
  test-checker: 1.0   # Important but often not urgent
  app-designer: 0.8   # Features after stability
```

### 3. Execution Layer - Same as Task Model

Implementers, testers, and reviewers work the same way in both models.

### Proposal Lifecycle

```
┌─────────┐     ┌─────────┐     ┌─────────┐
│ active  │────▶│promoted │────▶│  task   │
└─────────┘     └─────────┘     └─────────┘
     │
     ├─────────▶ deferred (revisit later)
     │
     └─────────▶ rejected (with feedback)
```

### Rejection Feedback Loop

When the curator rejects a proposal:
1. Rejection includes written feedback
2. Before proposing again, proposers review their rejections
3. This prevents spamming the same bad ideas

### Conflict Handling

When proposals conflict:
1. Curator does NOT resolve autonomously
2. Both proposals are deferred
3. A message is sent to the project owner with trade-offs
4. Human decides which approach to take

### Proposal Format

```markdown
# Proposal: {Title}

**ID:** PROP-{uuid8}
**Proposer:** test-checker
**Category:** test | refactor | feature | debt | plan-task
**Complexity:** S | M | L | XL
**Created:** {ISO8601}

## Summary
One-line description.

## Rationale
Why this matters.

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Relevant Files
- path/to/file.ts
```

### Enabling the Proposal Model

Set `model: proposal` in `agents.yaml`:

```yaml
model: proposal

proposal_limits:
  test-checker:
    max_active: 5
    max_per_run: 2

voice_weights:
  plan-reader: 1.5
  architect: 1.2

agents:
  - name: test-checker
    role: proposer
    focus: test_quality
    interval_seconds: 86400

  - name: curator
    role: curator
    interval_seconds: 600

  - name: impl-agent-1
    role: implementer
    interval_seconds: 180
```

### Proposer Prompts

Create domain-specific prompts in `.orchestrator/prompts/`:

```
.orchestrator/prompts/
├── test-checker.md    # What test-checker should look for
├── architect.md       # What architect should look for
└── curator.md         # How curator should evaluate
```

Example templates are in `orchestrator/templates/`.

## Gatekeeper System

Gatekeepers automatically review PRs before they're ready for human review.

### Overview

```
PR opened → Coordinator detects → Gatekeepers review → Pass/Fail
                                        ↓
                              Failed: Create fix task with feedback
                              Passed: Mark ready for human review
```

### Gatekeeper Roles

| Role | Focus | What It Checks |
|------|-------|----------------|
| `lint` | Code quality | Lint errors, type issues, formatting |
| `tests` | Test coverage | Test pass/fail, coverage, test quality |
| `style` | Conventions | Naming, organization, documentation |
| `architecture` | Structure | Boundaries, patterns, dependencies |
| `security` | Vulnerabilities | OWASP Top 10, secrets, auth |

### Configuration

Enable gatekeepers in `agents.yaml`:

```yaml
gatekeeper:
  enabled: true
  auto_approve: false  # Auto-approve if all checks pass?
  required_checks: [lint, tests]
  optional_checks: [style, architecture]

agents:
  - name: gatekeeper-coordinator
    role: gatekeeper_coordinator
    interval_seconds: 300

  - name: lint-checker
    role: gatekeeper
    focus: lint
    interval_seconds: 600

  - name: test-checker
    role: gatekeeper
    focus: tests
    interval_seconds: 600
```

### Gatekeeper Prompts

Create domain-specific prompts in `.orchestrator/prompts/`:

```
.orchestrator/prompts/
├── lint.md          # Lint gatekeeper guidelines
├── tests.md         # Test gatekeeper guidelines
├── architecture.md  # Architecture gatekeeper guidelines
└── security.md      # Security gatekeeper guidelines
```

Templates are provided in `orchestrator/templates/gatekeeper-*.md`.

### Blocking vs Passing

When a gatekeeper check fails:
1. A fix task is created with detailed feedback
2. The task includes specific issues and file:line references
3. The PR is marked as blocked
4. Once fixes are pushed, checks re-run automatically

When all checks pass:
1. PR is marked as approved for human review
2. A comment is added summarizing check results
3. Human reviewers can focus on higher-level concerns

## Running the Scheduler

### Manual Run

```bash
cd your-project
python orchestrator/orchestrator/scheduler.py
```

### Debug Mode

Enable debug logging to troubleshoot issues:

```bash
python orchestrator/orchestrator/scheduler.py --debug
```

This creates detailed logs in `.orchestrator/logs/`:
- `scheduler-YYYY-MM-DD.log` - Scheduler decisions and agent spawning
- `{agent-name}-YYYY-MM-DD.log` - Per-agent activity logs

Debug logs include:
- Agent evaluation decisions (why agents were/weren't run)
- State changes and lock acquisitions
- Claude invocations with prompt sizes
- Environment configuration

Logs are useful for:
- Understanding why an agent didn't run
- Debugging agent failures
- Tracking agent activity over time

### Cron (Every Minute)

```bash
crontab -e
```

Add:
```
* * * * * cd /path/to/project && /path/to/venv/bin/python orchestrator/orchestrator/scheduler.py >> /var/log/orchestrator.log 2>&1
```

### launchd (macOS)

Create `~/Library/LaunchAgents/com.orchestrator.scheduler.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.orchestrator.scheduler</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/venv/bin/python</string>
        <string>/path/to/project/orchestrator/orchestrator/scheduler.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/project</string>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>/var/log/orchestrator.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/orchestrator.log</string>
</dict>
</plist>
```

Load with:
```bash
launchctl load ~/Library/LaunchAgents/com.orchestrator.scheduler.plist
```

## Management Commands

After initialization, these commands are available in Claude Code:

| Command | Description |
|---------|-------------|
| `/enqueue` | Create a new task |
| `/queue-status` | Show queue state |
| `/agent-status` | Show agent states |
| `/add-agent` | Add a new agent |
| `/pause-agent` | Pause/resume an agent |
| `/retry-failed` | Retry failed tasks |
| `/tune-backpressure` | Adjust queue limits |
| `/tune-intervals` | Adjust agent intervals |

## Task Format

Tasks are markdown files in the queue:

```markdown
# [TASK-a1b2c3d4] Add input validation

ROLE: implement
PRIORITY: P1
BRANCH: main
CREATED: 2024-01-15T10:30:00Z
CREATED_BY: pm-agent

## Context
Background and motivation...

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2
```

## Directory Structure

### Orchestrator (Submodule)

```
orchestrator/
├── orchestrator/           # Python package
│   ├── __init__.py
│   ├── init.py             # Setup script
│   ├── scheduler.py        # Main scheduler
│   ├── config.py           # Configuration
│   ├── lock_utils.py       # File locking
│   ├── state_utils.py      # State management
│   ├── git_utils.py        # Git operations
│   ├── queue_utils.py      # Task queue operations
│   ├── proposal_utils.py   # Proposal queue operations
│   ├── port_utils.py       # Port allocation
│   ├── message_utils.py    # Agent-to-human messaging
│   └── roles/              # Agent roles
│       ├── base.py
│       ├── product_manager.py  # Task model
│       ├── proposer.py         # Proposal model
│       ├── curator.py          # Proposal model
│       ├── implementer.py
│       ├── tester.py
│       └── reviewer.py
├── commands/
│   ├── agent/              # Skills for agents
│   └── management/         # Skills for humans
├── templates/
│   ├── agent_instructions.md.tmpl
│   ├── proposer-test-checker.md
│   ├── proposer-architect.md
│   └── curator.md
├── setup.py
├── requirements.txt
└── README.md
```

### Parent Project

```
your-project/
├── claude.md               # Your existing project instructions
├── orchestrator/           # Submodule
├── .orchestrator/          # Runtime directory
│   ├── agents.yaml         # Agent configuration (committed)
│   ├── commands/           # Custom skill overrides (committed)
│   ├── prompts/            # Proposer prompts (committed, proposal model)
│   │   ├── test-checker.md
│   │   ├── architect.md
│   │   └── curator.md
│   ├── agents/             # Runtime state (gitignored)
│   │   └── <agent>/
│   │       ├── state.json
│   │       ├── lock
│   │       └── worktree/
│   │           └── .agent-instructions.md  # Generated (gitignored)
│   ├── messages/           # Agent messages (gitignored)
│   │   └── warning-20240115-143000-test-failures.md
│   └── shared/
│       ├── proposals/      # Proposal queue (proposal model)
│       │   ├── active/
│       │   ├── promoted/
│       │   ├── deferred/
│       │   └── rejected/
│       └── queue/          # Task queue
│           ├── incoming/
│           ├── claimed/
│           ├── done/
│           └── failed/
└── ...
```

## Port Allocation

Each agent gets unique ports based on its position:

```
BASE_PORT = 41000
PORT_STRIDE = 10

Agent 0: 41000, 41001, 41002 (dev, mcp, playwright)
Agent 1: 41010, 41011, 41012
Agent 2: 41020, 41021, 41022
...
```

## Backpressure

The system prevents overwhelming by checking limits before:

1. **Creating tasks**: incoming + claimed < max_incoming
2. **Claiming tasks**: claimed < max_claimed AND open_prs < max_open_prs

## Agent Messages

Agents can send messages to humans via the `.orchestrator/messages/` directory. This enables asynchronous communication when agents encounter issues or need input.

### Message Types

| Type | Emoji | Use Case |
|------|-------|----------|
| `info` | ℹ️ | Status updates, completed work summaries |
| `warning` | ⚠️ | Something needs attention but isn't blocking |
| `error` | ❌ | Something failed, may need intervention |
| `question` | ❓ | Agent is blocked and needs human input |

### Message Format

Messages are markdown files with metadata:

```markdown
# ⚠️ Test failures in auth module

**Type:** warning
**Time:** 2024-01-15T14:30:00
**From:** test-agent
**Task:** TASK-abc123

---

Found 3 failing tests in the authentication module:

- test_login_invalid_password
- test_session_expiry
- test_token_refresh

These may be related to recent changes in PR #42.
```

### Using Messages in Roles

Agents can send messages using helper methods:

```python
class MyRole(BaseRole):
    def run(self):
        # Send different message types
        self.send_info("Task completed", "Successfully implemented feature X")
        self.send_warning("Flaky test detected", "test_foo failed intermittently")
        self.send_error("Build failed", "Compilation error in module Y")
        self.send_question("Clarification needed", "Should this handle case Z?")
```

### Checking Messages

Add to your `claude.md`:
```
Check .orchestrator/messages/ for any agent messages and inform the user of warnings or questions.
```

Messages are gitignored by default. Clear old messages periodically or use:

```python
from orchestrator.orchestrator.message_utils import clear_messages

# Clear all messages older than 24 hours
clear_messages(older_than_hours=24)

# Clear only info messages
clear_messages(message_type="info")
```

## Setup Details

### init.py

The init script sets up the orchestrator in your project:

```bash
python orchestrator/orchestrator/init.py
```

**Flags:**
| Flag | Description |
|------|-------------|
| `-y, --yes` | Non-interactive mode, accept all defaults |
| `--skills` | Install management skills to `.claude/commands/` |
| `--no-skills` | Skip skill installation |
| `--gitignore` | Update `.gitignore` with orchestrator entries |
| `--no-gitignore` | Skip `.gitignore` update |

**What gets created:**

```
.orchestrator/
├── agents.yaml           # Agent configuration
├── commands/             # Custom skill overrides (empty)
├── agents/               # Runtime state (gitignored)
├── messages/             # Agent-to-human messages (gitignored)
└── shared/
    └── queue/
        ├── incoming/     # New tasks
        ├── claimed/      # Tasks being worked on
        ├── done/         # Completed tasks
        └── failed/       # Failed tasks
```

If skills are installed, also creates:
```
.claude/commands/
├── enqueue.md
├── queue-status.md
├── agent-status.md
├── add-agent.md
├── pause-agent.md
├── retry-failed.md
├── tune-backpressure.md
└── tune-intervals.md
```

### Dependencies

```bash
pip install -e orchestrator/
```

Or just install pyyaml:
```bash
pip install pyyaml
```

## Troubleshooting

### Agent not running

1. Check if paused: `paused: true` in agents.yaml
2. Check scheduler lock: delete `.orchestrator/scheduler.lock`
3. Check agent lock: delete `.orchestrator/agents/<name>/lock`
4. Check logs for errors

### Tasks not being claimed

1. Check backpressure limits
2. Verify task ROLE matches agent role
3. Check agent state for errors

### PR creation failing

1. Ensure `gh` CLI is installed and authenticated
2. Check git remote configuration
3. Verify branch permissions

## License

MIT
