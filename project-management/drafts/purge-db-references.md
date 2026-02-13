# Purge All Database References from Octopoid

**Status:** Idea
**Captured:** 2026-02-13

## Raw

> the project has gone through various incarnations to reach where it is now. There are still a lot of vestiges of former versions. Eg, there should be no mention of dbs anywhere in here. We need to audit this and clean up

## Idea

Octopoid v2.0 moved to a Cloudflare Workers + D1 API-only architecture. The local SQLite database (`orchestrator/db.py`, `state.db`, `is_db_enabled()` checks) is dead code from previous versions but references are scattered everywhere. All of it needs to be purged.

## Scope

1420 occurrences across 83 files. Key areas:

### Delete entirely
- `orchestrator/db.py` — full SQLite backend module (48 matches)
- `tests/test_db.py` — tests for the deleted module (220 matches)
- `project-management/drafts/sqlite-proposal.md` — obsolete RFC to add SQLite

### Production code to clean
- `orchestrator/config.py` — `is_db_enabled()`, `database` config section (14 matches)
- `orchestrator/queue_utils.py` — `is_db_enabled()` guards, `from .db` imports (21 matches)
- `orchestrator/planning.py` — `is_db_enabled()` checks (5 matches)
- `orchestrator/reports.py` — `is_db_enabled()` checks (9 matches)
- `orchestrator/approve_orch.py` — `from .db` imports, `is_db_enabled()` (6 matches)
- `orchestrator/review_orch.py` — db references (5 matches)
- `orchestrator/scheduler.py` — db references (21 matches)
- `orchestrator/migrate.py` — SQLite migration logic (49 matches)
- `orchestrator/roles/rebaser.py` — db guards (5 matches)
- `orchestrator/roles/pre_check.py` — db guards (5 matches)
- `orchestrator/roles/gatekeeper.py` — db guards (5 matches)
- `orchestrator/roles/orchestrator_impl.py` — db references (7 matches)
- `orchestrator/roles/breakdown.py` — db references (3 matches)
- `orchestrator/roles/recycler.py` — db references (4 matches)
- `scripts/*.py` — several scripts with db imports (approve_task, cancel_task, move_task, etc.)

### Test files to clean
- `tests/conftest.py` — db fixtures (13 matches)
- `tests/test_queue_utils.py` (59), `tests/test_rebaser.py` (102), `tests/test_approve_orch.py` (38), `tests/test_review_orch.py` (27), `tests/test_review_system.py` (91), `tests/test_gatekeeper_wiring.py` (60), `tests/test_orchestrator_impl.py` (34), and many more

### Config and docs
- `.octopoid/config.yaml` — `database:` section
- `DEVELOPMENT_RULES.md` — db references (18 matches)
- `docs/architecture.md`, `docs/architecture-v2.md`, `docs/migration-v2.md` — historical db mentions
- `README.md` — db references
- `commands/agent/*.md` — scattered db mentions

## Strategy

This is a large-scale cleanup best done in phases:

1. **Delete dead modules** — `orchestrator/db.py`, `tests/test_db.py`, `orchestrator/migrate.py`, `sqlite-proposal.md`
2. **Remove `is_db_enabled()` and config** — delete the function from `config.py`, remove `database:` from config.yaml, then fix all callers
3. **Clean production code** — remove all `from .db import` lines and db-conditional branches. Everything should go through SDK now
4. **Clean test code** — remove db fixtures from conftest, update test mocks to not reference db
5. **Clean scripts** — update or delete scripts that use `orchestrator.db`
6. **Clean docs and commands** — remove db mentions from markdown files
7. **Run tests** — verify nothing breaks

## Open Questions

- Should `orchestrator/migrate.py` be deleted entirely or repurposed for API migrations?
- Are any of the scripts in `scripts/` still used, or can some be deleted outright?
- Should docs that mention the old architecture be updated or just deleted?

## Possible Next Steps

- Break this into multiple tasks (one per phase above)
- Start with phase 1 (delete dead modules) as it's the safest and most satisfying
- Grep for `db` references after each phase to track progress
