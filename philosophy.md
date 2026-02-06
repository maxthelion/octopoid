# Philosophy

Octopoid exists because working with AI agents one-at-a-time doesn't scale. When you're pair-programming with Claude, it's easy to get sidetracked — you came to plan the next feature but ended up refactoring a utility module for an hour. Meanwhile the tests still need updating, the tech debt keeps growing, and the ideas you scribbled down last week haven't gone anywhere.

Octopoid is designed to fix this by turning a single developer's intent into coordinated, concurrent work.

## The problems

**Some things just need to happen regularly.** Tests need running, dependencies need updating, code quality needs monitoring. These shouldn't require you to remember to do them.

**Some things need to happen when conditions are met.** A new PR needs reviewing. A failing test needs a fix. A merged feature opens the door for the next one. These are reactive — they should trigger automatically.

**Working with an agent directly is absorbing.** It's easy to lose the forest for the trees. You sit down to plan and end up implementing. You meant to prioritise and ended up debugging. The time you spend in the weeds is time you're not spending on direction.

**Ideas pile up faster than they get done.** You have notes, todos, half-formed plans. Without a system to make sense of them and break them into actionable work, they just accumulate.

## What Octopoid aims for

**More gets done concurrently.** Multiple agents work in parallel, each focused on their own task. You're not the bottleneck any more.

**Work is pull-based, not push-based.** Agents pull work when they're ready for it. This prevents overload and means work flows at a sustainable pace.

**Specialised agents tackle different facets of the project.** One agent focuses on test quality, another on architecture, another on features. Each brings a different lens to the codebase, surfacing things you'd miss when you're heads-down on implementation.

**Suggestions are made ahead of time — but only to a limited degree.** Proposers surface opportunities and the curator decides what's worth doing now. The system prepares future work without letting speculative plans pile up endlessly.

**Wasted work doesn't accumulate.** Backpressure limits, curation, and quality gates mean the system only produces as much work as can be meaningfully reviewed and merged. No mountains of stale PRs. No buggy drive-by changes.

**Quality is maintained, not sacrificed.** PRs don't just appear — they go through gatekeepers that check for bugs, test coverage, and style. The goal is to produce work you'd actually want to merge, not work you have to clean up after.

**The agent you interact with delegates to others.** You set direction and priorities. The orchestrator breaks that down and distributes it. You stay at the level of intent, not implementation.

**Big projects get scheduled and sequenced properly.** Complex work gets broken into pieces that are tackled in the right order, with dependencies respected. You describe what you want built; the system figures out how to coordinate it.

**Your ideas get organised.** Plans, notes, and half-formed thoughts get turned into structured proposals that are evaluated, prioritised, and either acted on or explicitly set aside. Nothing just disappears into a backlog.
