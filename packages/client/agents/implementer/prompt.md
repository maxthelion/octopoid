# Task: [$task_id] $task_title

**Priority:** $task_priority
**Branch:** $task_branch

$retry_section

## Task Description

$task_content

$review_section

$continuation_section

## Global Instructions

$global_instructions

## Available Scripts

You have the following scripts available in `$scripts_dir/`:

- **`$scripts_dir/run-tests`** — Detect and run the project test suite. Use this to verify your changes during development.
- **`$scripts_dir/fail <reason>`** — Mark the task as failed if you cannot complete it. Pass a reason string.
- **`$scripts_dir/record-progress <note>`** — Record a progress note. Use this to save context if you're running low on turns.

$required_steps

## Implementation Guidelines

1. Read and understand the task description and acceptance criteria
2. Explore the codebase to understand the relevant code
3. Implement the changes with clear, atomic commits
4. Run tests to verify your changes work

- Follow existing code patterns and conventions
- Write tests for new functionality
- Make focused, atomic commits with clear messages
- Do not modify files outside the scope of the task

## Completing Your Work

When you have finished your implementation, output a clear summary of what you did and exit.
The orchestrator infers your result from your stdout — no result file needed.

Do NOT create PRs, push branches, or call any scripts to submit your work.
The orchestrator handles all of that automatically after you exit.
