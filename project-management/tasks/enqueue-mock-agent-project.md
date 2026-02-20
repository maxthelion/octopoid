# TODO: Enqueue mock Claude agent tests as a project

**When:** After the project system is fixed (TASK-projfix-1 through TASK-projfix-4)

**What:** Break down `project-management/drafts/40-2026-02-18-mock-claude-agents-in-tests.md` into sequenced tasks and enqueue them as a project with a shared feature branch.

**Source:** Draft 40 has a detailed design for mock agent test fixtures, including:
- `mock-agent.sh` script that simulates agent execution
- Scheduler lifecycle tests (happy path, failure, crash recovery)
- Edge case tests (merge conflicts, push failures)
- Converting mocked tests to scoped real-server tests

**Why a project:** This is multi-step work where each step builds on the previous, and we don't want partial implementations polluting the main branch. Same pattern as pool model and project system fix.
