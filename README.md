# Local Multi-Agent Scheduler

A file-driven orchestrator that runs on 1-minute ticks, evaluates configured agents, and runs them when overdue. Designed as a self-contained git submodule.

## Overview

This orchestrator manages multiple autonomous Claude Code agents that work on tasks in parallel. Each agent has a specific role:

- **Product Manager** - Analyzes the codebase and creates tasks
- **Implementer** - Claims tasks, implements features, creates PRs
- **Tester** - Runs tests and adds test coverage
- **Reviewer** - Reviews code for bugs and security issues

## Installation

### As a Git Submodule

```bash
cd your-project
git submodule add https://github.com/your-org/orchestrator.git orchestrator
cd orchestrator
pip install -e .
```

### Initialize in Your Project

```bash
python orchestrator/orchestrator/init.py
```

This creates:
- `.orchestrator/` directory structure
- Example `agents.yaml` configuration
- Management commands in `.claude/commands/`
- Required `.gitignore` entries

## Configuration

### Claude Instructions

The orchestrator uses your project's existing `claude.md` file. You just need to add one line to it:

```markdown
If .agent-instructions.md exists in this directory, read and follow those instructions.
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

## Running the Scheduler

### Manual Run

```bash
cd your-project
python orchestrator/orchestrator/scheduler.py
```

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
│   ├── queue_utils.py      # Queue operations
│   ├── port_utils.py       # Port allocation
│   └── roles/              # Agent roles
│       ├── base.py
│       ├── product_manager.py
│       ├── implementer.py
│       ├── tester.py
│       └── reviewer.py
├── commands/
│   ├── agent/              # Skills for agents
│   └── management/         # Skills for humans
├── templates/
│   └── agent_instructions.md.tmpl
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
│   ├── agents/             # Runtime state (gitignored)
│   │   └── <agent>/
│   │       ├── state.json
│   │       ├── lock
│   │       └── worktree/
│   │           └── .agent-instructions.md  # Generated (gitignored)
│   └── shared/
│       └── queue/
│           ├── incoming/   # New tasks
│           ├── claimed/    # Being worked
│           ├── done/       # Completed
│           └── failed/     # Failed
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
