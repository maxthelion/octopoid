# Split scheduler.prepare_task_directory: extract _render_prompt to isolate prompt-building (CCN 27 → ~12)

**Status:** Complete
**Author:** architecture-analyst
**Captured:** 2026-02-23

## Issue

`prepare_task_directory` in `orchestrator/scheduler.py` (lines 731–896) has CCN=27 and 166 lines because it merges two unrelated responsibilities into a single function:

1. **Directory scaffolding** — creates the task dir, cleans stale files, creates the git worktree, writes `task.json`, copies and templates agent scripts, and writes `env.sh`.
2. **Prompt rendering** — loads the prompt template, loads global instructions, loads agent-specific `instructions.md`, parses agent hooks from the task, builds a `required_steps` section, loads the task message thread, performs template substitution, and writes `prompt.md`.

The prompt-rendering section (lines 813–893) contains ~13 of the function's ~27 cyclomatic complexity points: two `isinstance` branches for hook parsing, three name-based hook branches, existence checks, a try/except around thread loading, and multiple `if`-guarded string appends. None of this is related to directory setup. It has grown organically and is now untestable in isolation — you can't test prompt rendering without setting up a full task directory.

## Current Code

The function's two halves are visually separate but not architecturally separated:

```python
# ---- half 1: scaffolding ----
def prepare_task_directory(task, agent_name, agent_config) -> Path:
    task_dir.mkdir(...)
    create_task_worktree(task)
    (task_dir / "task.json").write_text(...)
    for script in scripts_src.iterdir():   # copies + templates scripts
        ...
    (task_dir / "env.sh").write_text(...)   # writes env vars

# ---- half 2: prompt rendering (lines 813–893) crammed into same function ----
    prompt_template = prompt_template_path.read_text()
    global_instructions = gi_path.read_text() if gi_path.exists() else ""
    if instructions_md_path.exists():
        global_instructions += "\n\n" + instructions_md_path.read_text()
    hooks = task.get("hooks")
    if hooks:
        if isinstance(hooks, str):        # branch 1
            agent_hooks = [h for h in json.loads(hooks) if h.get("type") == "agent"]
        elif isinstance(hooks, list):     # branch 2
            agent_hooks = [h for h in hooks if h.get("type") == "agent"]
    if agent_hooks:
        for i, hook in enumerate(agent_hooks, 1):
            if hook["name"] == "run_tests":   # branch 3
                ...
            elif hook["name"] == "create_pr": # branch 4
                continue
            else:                             # branch 5
                ...
    if task_id_for_thread:
        try:
            thread_messages = get_thread(task_id_for_thread)   # branch 6–7
            review_section = format_thread_for_prompt(thread_messages)
        except Exception:
            ...
    prompt = Template(prompt_template).safe_substitute(...)
    (task_dir / "prompt.md").write_text(prompt)
    return task_dir
```

## Proposed Refactoring

Apply the **Extract Function** refactoring to separate prompt rendering into its own function. The prompt renderer is itself a small pipeline — load → augment → parse → assemble → substitute — which can optionally use the **Builder** pattern if further decomposition is warranted later.

```python
# orchestrator/scheduler.py

def _render_prompt(task: dict, agent_config: dict) -> str:
    """Build the rendered prompt string from template, instructions, hooks, and thread.

    Returns the fully substituted prompt text (not written to disk here).
    """
    agent_dir = agent_config.get("agent_dir")
    if not agent_dir or not (Path(agent_dir) / "prompt.md").exists():
        raise ValueError(f"Agent directory or prompt.md not found: {agent_dir}")

    prompt_template = (Path(agent_dir) / "prompt.md").read_text()
    global_instructions = _load_global_instructions(agent_dir)
    required_steps = _build_required_steps(task)
    review_section = _load_review_section(task.get("id", ""))

    return Template(prompt_template).safe_substitute(
        task_id=task.get("id", "unknown"),
        task_title=task.get("title", "Untitled"),
        task_content=task.get("content", ""),
        task_priority=task.get("priority", "P2"),
        task_branch=task.get("branch") or get_base_branch(),
        task_type=task.get("type", ""),
        scripts_dir="../scripts",
        global_instructions=global_instructions,
        required_steps=required_steps,
        review_section=review_section,
        continuation_section="",
    )


def prepare_task_directory(task, agent_name, agent_config) -> Path:
    """Prepare a self-contained task directory for script-based agents."""
    # ... scaffolding unchanged ...
    (task_dir / "prompt.md").write_text(_render_prompt(task, agent_config))
    return task_dir
```

The three helper functions split naturally from `_render_prompt`:

```python
def _load_global_instructions(agent_dir: str) -> str:
    """Load global + agent-specific instructions."""

def _build_required_steps(task: dict) -> str:
    """Parse agent hooks and build the required-steps section."""

def _load_review_section(task_id: str) -> str:
    """Load and format the task message thread."""
```

Each helper has CCN ≤ 4 and can be tested with a plain dict — no filesystem setup needed.

## Why This Matters

- **Testability:** `_render_prompt` and its helpers can be unit-tested with just `task` and `agent_config` dicts. Currently there is no way to test prompt rendering without scaffolding an entire task directory.
- **Readability:** `prepare_task_directory` becomes a short, linear sequence of directory operations. The prompt logic is no longer buried at line 813 of a 166-line function.
- **Debuggability:** When a rendered prompt is wrong, the bug is isolated to `_render_prompt` and its three helpers rather than anywhere in a 166-line function.
- **Independent evolution:** Prompt rendering (template format, hook handling, thread loading) can change without touching the directory setup code and vice versa.

## Metrics

- **File:** `orchestrator/scheduler.py`
- **Function:** `prepare_task_directory`
- **Current CCN:** 27 / Lines: 166
- **Estimated CCN after extraction:**
  - `prepare_task_directory`: ~12–14
  - `_render_prompt`: ~6–8
  - `_build_required_steps`: ~4
  - `_load_global_instructions`: ~2
  - `_load_review_section`: ~2
