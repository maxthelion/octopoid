---
**Processed:** 2026-02-22
**Mode:** human-guided
**Actions taken:**
- Audited codebase for all references to local message system
- All code migration already complete (TASK-a40d1ed3 removed message_utils.py and migrated all callers)
- Only remaining cleanup: delete stale `.octopoid/messages/` directory
**Outstanding items:** Delete `.octopoid/messages/` directory
---

# Remove Local Message System and Audit All Callers

**Status:** Complete
**Captured:** 2026-02-22
**Related:** Draft 78 (human inbox tab), Draft 34 (messages table)

## Raw

> Earlier we discussed the fact that clicking "process" on a draft had written to .octopoid/messages/info-20260222-115500-process-draft-69.md. This is incorrect behaviour. That messages directory should be deleted. Nothing should be posting to it. We need to audit any code that references it in any way and make sure that we are using the messages api on the server instead.

## Audit Results

**Code migration is fully complete.** TASK-a40d1ed3 (inbox tab) already did the work:

- `orchestrator/message_utils.py` — **deleted** (no longer exists)
- `message_utils` — **zero references** in any `.py` file
- `create_message` / `list_messages` / `_post_inbox_message` — **zero references** in any `.py` file
- All dashboard action buttons now post via `sdk.messages.create()`
- `_gather_messages()` in `reports.py` now reads from `sdk.messages.list(to_actor="human")`

**Remaining artifact:** `.octopoid/messages/` directory still exists on disk with one stale file (`info-20260222-115500-process-draft-69.md`). This was written before the migration landed. The directory should be deleted manually — it's not tracked by git.

## Resolved Questions

- **Is `message_utils.py` deleted?** Yes, confirmed.
- **Other callers?** None found. Full audit clean.
- **Is `.octopoid/messages/` in `.gitignore`?** It's under `.octopoid/` which is gitignored. Just needs manual `rm -rf`.
