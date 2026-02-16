# Task: [$task_id] $task_title

**Priority:** $task_priority
**Branch:** $task_branch

## Task Description

$task_content

$review_section

$continuation_section

## Global Instructions

$global_instructions

## Available Scripts

You have the following scripts available in `$scripts_dir/`:

- **`$scripts_dir/submit-pr`** — Push your branch and create a pull request. Records hook evidence with the server. Call this when your implementation is complete and tests pass.
- **`$scripts_dir/run-tests`** — Detect and run the project test suite. Records results as hook evidence. Fix any failures before submitting.
- **`$scripts_dir/finish`** — Mark the task as complete. Call this after submit-pr succeeds.
- **`$scripts_dir/fail <reason>`** — Mark the task as failed if you cannot complete it. Pass a reason string.
- **`$scripts_dir/record-progress <note>`** — Record a progress note. Use this to save context if you're running low on turns.

$required_steps

## Implementation Guidelines

1. Read and understand the task description and acceptance criteria
2. Explore the codebase to understand the relevant code
3. Implement the changes with clear, atomic commits
4. Run tests to verify your changes work
5. Submit a PR when complete

- Follow existing code patterns and conventions
- Write tests for new functionality
- Make focused, atomic commits with clear messages
- Do not modify files outside the scope of the task
