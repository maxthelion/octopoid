# Agent Failure Postmortems

Log of cases where agents built the wrong thing or failed to address review feedback. Used to identify patterns and improve task descriptions.

## Format

Each file: `<YYYY-MM-DD>-<task-id>.md`

## Template

```markdown
# <Task title>

**Task:** <task ID>
**Agent:** <agent name>
**Attempts:** <number>
**Outcome:** wrong-target | incomplete | ignored-feedback | crashed

## What was asked
<1-2 sentences>

## What the agent built
<1-2 sentences>

## Why it went wrong
<Root cause — was the task unclear? Too broad? Contradictory? Wrong assumptions?>

## Lesson
<What to do differently in task descriptions>
```
