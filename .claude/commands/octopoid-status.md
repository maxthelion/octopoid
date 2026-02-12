# Octopoid Queue Status

Run the orchestrator status script and interpret the output for the user.

## Instructions

1. Run the status script:
```bash
.venv/bin/python scripts/octopoid-status.py $ARGUMENTS
```

2. Analyze the output and provide a concise summary covering:
   - **System health**: Is the scheduler running? Is the system paused?
   - **Queue state**: How many tasks in each queue? Are limits being respected?
   - **Agent activity**: Which agents are running/idle/blocked? Any errors?
   - **Concerns**: Flag any stuck tasks (claimed >2h with 0 commits), stale provisional tasks, agents with repeated failures, rate limiting errors, or backpressure blocks.

3. If there are actionable issues, suggest specific fixes using the `octopoid` CLI (e.g., `octopoid requeue <id>`, `octopoid cancel <id>`).

## Arguments

- No arguments: full status report
- `--verbose` or `-v`: include worktree commit logs and expanded notes
- `--logs N`: show N lines of recent logs (default 10)
- `--task <id>`: show detailed info for a specific task
