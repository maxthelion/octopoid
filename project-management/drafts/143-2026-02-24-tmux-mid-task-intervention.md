# Mid-Task Agent Intervention via tmux

**Captured:** 2026-02-24

## Raw

> The tmux mid-task intervention idea is the most actionable takeaway. Right now our only option for a misdirected agent is to let it finish, get rejected, and start over. Draft about tmux and feasibility. That's the bit that stuck out for me, but I don't fully understand it. Explain in the draft.

## What is tmux and how does it enable this?

**tmux** (terminal multiplexer) is a program that creates persistent terminal sessions that survive
disconnection. Think of it as a "headless terminal" — a shell session that runs in the background
and can be attached to, detached from, and crucially, **sent keystrokes programmatically**.

The key capability: `tmux send-keys` lets you type into a running terminal session from outside.

### How the OpenClaw system uses it

Instead of spawning `claude -p "prompt"` as a subprocess (what we do), they spawn it inside a tmux
session:

```bash
# Create a new tmux session named "codex-templates" and run the agent in it
tmux new-session -d -s "codex-templates" \
  "claude --model claude-opus-4.5 --dangerously-skip-permissions -p 'Your prompt here'"
```

The `-d` flag means "detached" — it runs in the background, just like our `subprocess.Popen`. But
because it's in a tmux session, you can interact with it:

```bash
# See what the agent is doing right now (attach to the session)
tmux attach -t codex-templates

# Send a message to the agent WITHOUT attaching
tmux send-keys -t codex-templates "Stop. Focus on the API layer first, not the UI." Enter
```

That `send-keys` command literally types text into the terminal where the agent is running. If the
agent is in an interactive mode (like Claude Code's REPL), this text becomes input to the agent.
The `Enter` at the end simulates pressing the Enter key to submit the message.

### Why this is powerful

Right now in octopoid, agents are black boxes. We spawn them with `subprocess.Popen`, pipe stdout
and stderr to log files, and wait for them to finish. Our only options are:

1. **Let it finish** — hope it does the right thing
2. **Kill it** — `os.kill(pid, signal.SIGTERM)`, lose all work
3. **Wait for gatekeeper** — agent finishes, gatekeeper rejects, agent restarts from scratch

With tmux, there's a fourth option:

4. **Redirect mid-flight** — send a message to the running agent: "You're going the wrong
   direction. The customer wanted X, not Y." The agent receives this as new input and adjusts.

This is the difference between a missile (fire and forget) and a drone (course-correct in flight).

## How octopoid currently spawns agents

`orchestrator/scheduler.py:992`:

```python
process = subprocess.Popen(
    cmd,
    cwd=worktree_path,
    env=env,
    stdout=stdout_file,
    stderr=stderr_file,
    start_new_session=True,
)
```

The agent runs as a detached subprocess. stdout/stderr go to log files. We record the PID and
check if it's still alive later. No interaction is possible.

The `cmd` is something like:
```
claude -p "prompt text" --model sonnet --max-turns 150 --allowedTools ...
```

The `-p` flag runs Claude Code in **non-interactive (pipe) mode** — it reads the prompt, does its
work, and exits. There's no REPL to send messages to.

## Feasibility for octopoid

### The core question: does `claude -p` accept mid-flight input?

**No.** When Claude Code runs with `-p` (pipe mode), it reads the prompt from the argument and
runs to completion. It doesn't read from stdin. `tmux send-keys` would type into the terminal
but the claude process wouldn't see it.

For tmux intervention to work, the agent would need to run in **interactive mode** (no `-p` flag)
where Claude Code presents a REPL and reads from stdin. The OpenClaw article uses interactive
mode — they type into the tmux session and the agent reads it as user input.

### What would need to change

1. **Spawn in tmux instead of subprocess.Popen**
   ```python
   # Instead of subprocess.Popen(cmd, ...)
   subprocess.run([
       "tmux", "new-session", "-d",
       "-s", f"octopoid-{task_id}",
       " ".join(cmd_without_p_flag),
   ])
   ```

2. **Use interactive mode, not pipe mode**
   Instead of `claude -p "prompt"`, run `claude` interactively and send the prompt via tmux:
   ```bash
   tmux new-session -d -s "octopoid-task123" "claude --model sonnet --max-turns 150"
   # Wait a moment for startup
   tmux send-keys -t "octopoid-task123" "$(cat prompt.md)" Enter
   ```

3. **PID tracking changes** — we'd track tmux session names instead of PIDs. Check if alive via
   `tmux has-session -t "octopoid-task123"`.

4. **Log capture changes** — tmux can log output via `tmux pipe-pane -t session-name "cat >> logfile"`,
   but it captures the raw terminal output (including ANSI escape codes). Parsing would be messier
   than our current clean stdout/stderr files.

5. **Result detection** — currently we read `result.json` after the process exits. With tmux,
   we'd need to detect when the agent is done (session closed? specific output pattern?).

6. **Intervention API** — add a way for the scheduler, dashboard, or CLI to send messages to
   running agents:
   ```python
   def send_to_agent(task_id: str, message: str):
       session = f"octopoid-{task_id}"
       subprocess.run(["tmux", "send-keys", "-t", session, message, "Enter"])
   ```

### Challenges

- **tmux must be installed** — it's standard on macOS and Linux but adds a dependency
- **Log parsing** — raw terminal output includes ANSI codes, line wrapping, etc.
- **Agent completion detection** — harder than checking if a PID is alive
- **Prompt injection risk** — sending arbitrary text to a running agent is a security surface.
  A malicious message could override the agent's instructions.
- **Timing** — if you send a message while the agent is mid-tool-call, it may not see it until
  the tool finishes. The agent needs to be "listening" (waiting for user input) for send-keys
  to work as expected.
- **Claude Code in interactive mode behaves differently** — it shows a TUI, expects human-style
  interaction, has approval prompts. Running it headless-interactive in tmux may have quirks.
- **`--dangerously-skip-permissions`** — required for unattended interactive mode, same as `-p`

### A simpler alternative: file-based messaging

Instead of tmux, we could use a file-based intervention mechanism:

1. Scheduler writes a message to `.octopoid/runtime/tasks/{task-id}/intervention.md`
2. A Claude Code hook (`PostToolUse` or similar) checks for this file after each tool call
3. If found, it reads the message, appends it to the conversation, and deletes the file

This would work with our existing `subprocess.Popen` approach — no tmux needed. But it requires
Claude Code hook support for reading external files mid-conversation, which may or may not work
cleanly.

### Another alternative: Claude Code's `--continue` flag

Claude Code has a `--continue` / `-c` flag that continues the most recent conversation. In theory:

1. Agent runs with `-p "initial prompt"` and finishes (or is killed)
2. Orchestrator runs `claude -c -p "correction: focus on X not Y"` in the same directory
3. Claude Code picks up where it left off with the new instruction

This gives intervention without tmux, but it's not truly mid-flight — you'd have to wait for the
agent to finish or kill it first.

## Open Questions

- Does Claude Code's interactive mode work reliably when spawned inside tmux without a real TTY?
- Would the `--continue` approach be good enough, or do we genuinely need mid-flight intervention?
- Is the file-based hook approach simpler and sufficient?
- How does the OpenClaw author handle the "agent is mid-tool-call and can't see input" timing issue?
- Would this work with Codex too, or is it Claude Code specific?

## Possible Next Steps

- Spike: spawn Claude Code interactively in tmux, try sending a message mid-task, see what happens
- Investigate Claude Code hooks as an intervention mechanism (no tmux needed)
- If tmux works: prototype `send_to_agent()` function and a dashboard "intervene" button
- If tmux doesn't work well: explore the `--continue` approach as a simpler alternative
