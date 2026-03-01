# Feed code quality tools into codebase analyst agents

**Captured:** 2026-02-28

## Raw

> I want to try to use these tools we've just tried to feed into the codebase analyst. They should look at all the outputs and determine the most high impact thing we can do. As well as writing a draft. Let's have them /enqueue tasks where they deem it appropriate.

## Idea

Give the codebase analyst agents access to code quality tools — pytest-cov, pydeps, vulture, and wily — so they can run them, interpret the results, and propose high-impact improvements. Instead of just reading code and writing drafts, analysts would have quantitative data to back their recommendations and could enqueue tasks directly when the fix is clear-cut.

### Tools and what they reveal

- **pytest-cov**: Coverage gaps. Current state: 63% overall, scheduler.py at 53%, jobs.py at 24%, git_utils.py at 55%
- **pydeps**: Dependency graph. Reveals circular deps (queue_utils ↔ git_utils, steps → scheduler, task_thread → scheduler) and inverted dependencies
- **vulture**: Dead code. Found 23 unused re-exports in queue_utils.py, unused imports in scheduler.py
- **wily**: Complexity and maintainability trends. scheduler.py has MI of 0.08 (effectively 0), cyclomatic complexity of 313, 2287 lines

### What the analyst would do

1. Run the tools against the `octopoid/` package
2. Parse and cross-reference the outputs (e.g. a file with low MI + low coverage + circular deps = highest priority)
3. Write a draft explaining the findings and recommending the highest-impact change
4. Enqueue tasks for clear fixes (e.g. "remove 23 dead re-exports from queue_utils.py", "extract X from scheduler.py into its own module")

## Context

We just ran all four tools manually in a session and found significant issues: scheduler.py is unmaintainable (MI: 0.08), queue_utils.py is a facade nobody uses (23 dead re-exports creating circular deps), and several modules have inverted dependencies on scheduler. The analyst agents currently only read code — giving them tools would let them find these issues automatically and prioritize by impact.

### Model

Use **Opus** for this agent. It needs to interpret multiple tool outputs, cross-reference findings across tools, and make judgement calls about what to prioritise. Sonnet would likely miss the connections between e.g. a file having low MI + circular deps + low coverage all pointing to the same root cause.

## Open Questions

- Should each analyst run all tools, or should we have a dedicated "code quality analyst" agent?
- How do we ensure the tools are installed in the agent's environment? The agents run via Claude CLI — they'd need pytest-cov, pydeps, vulture, and wily available.
- Should the analyst be allowed to `/enqueue` directly, or should it propose tasks in a draft for human review? Direct enqueue is faster but risks low-quality tasks. Proposed tasks in a draft give the human a checkpoint.
- What thresholds trigger action? e.g. "any file with MI < 20 gets a refactor task", "any function with cyclomatic complexity > 15 gets flagged"
- wily needs a clean git state to build its index — how does this work in an agent worktree?

## Invariants

- `analyst-uses-quantitative-tools`: The codebase analyst agent runs code quality measurement tools (pytest-cov for coverage, pydeps for dependency graphs, vulture for dead code, wily for complexity trends) before proposing refactoring or quality improvement drafts. Proposals include quantitative data supporting the recommendation, not just qualitative code reading.

## Possible Next Steps

- Add the four tools to the agent environment (pip install in the venv or system-wide)
- Write a script that runs all four tools and outputs a structured summary (JSON or markdown)
- Update the codebase analyst prompt to include running the script and interpreting results
- Define thresholds and rules for when to draft vs when to enqueue
- Test with a single run and review the quality of the analyst's recommendations
