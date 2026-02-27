# Activity Indicators on Kanban Task Cards

**Status:** Superseded
**Archived:** 2026-02-22
**Captured:** 2026-02-22

## Raw

> Are there some spinners or similar that we can add to tasks that are working so that we see that there is activity going on on the kanban board. Add spinners or animation to task cards on the kanban board to show that an agent is actively working on them. Could pulse the card, add a spinner character, or show a "last active X ago" timestamp. The goal is to distinguish "claimed and actively being worked on" from "claimed but stalled/stuck".

## Idea

Task cards on the kanban board are static — a claimed task looks the same whether the agent is actively working or has been stuck for an hour. Add visual activity indicators so you can tell at a glance what's alive and what needs attention.

Options (not mutually exclusive):
- **Spinner character** — a cycling Unicode spinner (e.g. `|/-\` or braille dots `⠋⠙⠹⠸`) next to claimed tasks, driven by a Textual timer
- **"Last active" timestamp** — show "2m ago" or "45m ago" based on the agent's last heartbeat or last log entry
- **Colour/style change** — claimed cards that haven't had activity in >10 minutes get a warning colour (orange border, dimmed text)
- **Progress text** — if the agent reports progress (e.g. "running tests", "creating PR"), show it on the card

## Context

Came up while watching the kanban board with multiple tasks in flight. Everything looks the same — you can't tell if agents are making progress, waiting, or stuck. The dashboard already polls for state changes, so adding a heartbeat-derived indicator should be straightforward.

## Open Questions

- What's the best data source for "last active"? Options: agent PID heartbeat, scheduler last_heartbeat, task `claimed_at` timestamp, log file modification time
- Should stale tasks (no activity for N minutes) get a visual warning automatically?
- Does Textual support animated spinners natively, or do we need a custom timer widget?
- Should this extend to provisional tasks too (showing gatekeeper review progress)?

## Possible Next Steps

- Check what activity data is already available (heartbeats, timestamps, PID checks)
- Prototype a spinner widget in Textual using `set_interval`
- Add a "last_active" or "claimed_at" display to task cards
- Colour-code cards by staleness (green = recent activity, orange = getting stale, red = stuck)
