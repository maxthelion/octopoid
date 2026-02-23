# Architecture Analyst Agent

**Status:** Idea
**Captured:** 2026-02-23

## Raw

> An analyst that looks at the project from an architectural point of view. It should compare it with architectural patterns used elsewhere. Look at where we are being repetitive or not abstracting things. Try to suggest OOP, functional etc over big nested if statements. Avoid large functions etc.

## Idea

A background analyst agent (like the codebase analyst and testing analyst) that periodically reviews the codebase for architectural quality. It runs on a daily schedule, analyses source files, and proposes refactoring improvements as drafts.

### What it looks for

1. **Large functions** — functions over a threshold (e.g. 50+ lines). Suggest splitting into smaller, composable pieces. Flag the worst offenders.

2. **Deeply nested control flow** — nested if/elif/else chains, nested loops, callback pyramids. Suggest early returns, guard clauses, pattern matching, or strategy patterns.

3. **Code repetition** — similar code blocks appearing across multiple files. Suggest extracting shared utilities, base classes, or higher-order functions.

4. **Missing abstractions** — places where OOP (protocols, base classes, composition) or functional patterns (map/filter, decorators, closures) would simplify the code. Concrete things like: handler registries that could be decorator-based, switch statements that could be polymorphic dispatch, repeated try/except blocks that could be context managers.

5. **God objects / modules** — files that do too many things. Suggest responsibility splits (compare with the codebase analyst, which already finds large files — this agent focuses on *why* they're large and *how* to split them).

6. **Comparison with established patterns** — reference well-known architectural patterns (strategy, observer, command, pipeline, etc.) and suggest where they'd fit. Compare with how similar systems (task queues, schedulers, agent frameworks) typically structure their code.

### Tooling

The analysis script should use established static analysis tools rather than hand-rolled grep patterns:

- **Lizard** (`pip install lizard`) — function-level metrics in a single pass: line count (NLOC), cyclomatic complexity, nesting depth, parameter count. Multi-language (Python + TypeScript). JSON output. Built-in thresholds (`-T nloc=50 -T cyclomatic_complexity=10`). Covers categories 1, 2, and 5.
- **jscpd** (`npm install -g jscpd`) — copy-paste detection across 150+ languages. Finds duplicated code blocks across multiple files with JSON output. Covers category 3.

Example scan script usage:
```bash
# Functions over 50 lines or CC > 10, JSON output
lizard orchestrator/ --json -T nloc=50 -T cyclomatic_complexity=10

# Duplicated blocks, min 5 lines, JSON report
jscpd orchestrator/ --min-lines 5 --reporters json --output /tmp/jscpd-report/
```

The agent reads the structured output from these tools and uses its own reasoning (this is where the LLM adds value) to identify missing abstractions, suggest design patterns, and propose concrete refactorings with before/after examples.

### How it works

Same pattern as codebase analyst and testing analyst:
- Guard script: skip if there's already an unresolved architecture proposal draft
- Analysis script: runs Lizard + jscpd, outputs a structured report of the worst offenders
- Agent reads the report, picks the single most impactful improvement, writes a draft proposing the specific refactoring with before/after code examples
- Attaches actions (Enqueue refactoring task / Dismiss) and posts to inbox

### Avoiding repetition and diminishing returns

- The agent should read its own previous drafts (via `sdk.drafts.list()` filtered by `author=architecture-analyst`) before proposing. If a file or pattern has already been flagged in a previous draft (even a superseded one), skip it and look for the next candidate.
- If after scanning nothing meets the threshold for a useful proposal, the agent should exit cleanly without creating a draft. Not every run needs to produce output — reporting "things look fine" is a valid outcome. The guard script handles this implicitly (no draft = agent runs next tick), but the prompt should also explicitly allow a clean exit when there's nothing worth flagging.

### Key principle

Proposals should be concrete and specific — not "this file is too long" but "extract the flow dispatch logic from scheduler.py into a FlowDispatcher class with methods X, Y, Z". Include before/after sketches so the human can evaluate the trade-off.

## Context

The orchestrator codebase has grown rapidly through agent-driven development. Agents tend to add code that works but doesn't always follow the best architectural patterns — they optimise for "make it work" over "make it clean". Examples: `scheduler.py` is 2600+ lines with many large functions, `result_handler.py` has deeply nested conditional chains, several modules have similar SDK-calling patterns that could be abstracted.

The codebase analyst (Draft 69) already finds large files, but it only measures size — it doesn't understand *why* a file is large or *what* pattern would fix it. This agent fills that gap.

## Open Questions

- Should it have a different model than Sonnet? Architecture analysis might benefit from a more capable model (Opus) for deeper reasoning about design patterns.
- Should it focus on one category per run (e.g. "large functions" one day, "repetition" the next) or scan everything and pick the worst?
- How does it avoid contradicting existing architectural decisions? It needs to read CLAUDE.md and docs/ to understand intentional design choices.
- Should it also look at the test code, or only production code?
- What's the relationship with the codebase analyst — should they merge into one agent with multiple analysis modes, or stay separate?

## Possible Next Steps

- Create agent config, scripts, and prompt following the testing-analyst pattern
- Write the analysis script(s) — likely needs multiple passes (function length, nesting depth, duplication detection)
- Write the prompt emphasising concrete proposals with before/after examples
- Consider whether to use Opus for deeper architectural reasoning
