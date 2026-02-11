# Implementation Plan: Per-Task Logs and Enhanced Status Script

## High-Level Approach

This task adds per-task logging infrastructure to track the complete lifecycle of each task across all state transitions. The approach follows the phases outlined in the GitHub issue:

1. Create TaskLogger infrastructure for persistent per-task logs
2. Wire logging into all queue operations (create, claim, submit, reject, etc.)
3. Enhance status scripts to display claim history and file paths
4. Consider DB schema updates for claim_count tracking

## Implementation Steps

- [x] 1. Create TaskLogger class with log file management
  - Created `orchestrator/task_logger.py`
  - Implemented log directory creation (`.orchestrator/logs/tasks/`)
  - Implemented timestamped log entry formatting
  - Added methods: `log_created()`, `log_claimed()`, `log_submitted()`, `log_rejected()`, `log_accepted()`, `log_failed()`, `log_escalated()`, `log_recycled()`
  - Added helper methods: `get_claim_count()`, `get_claim_times()`

- [x] 2. Wire TaskLogger into queue_utils.py
  - Imported TaskLogger in queue_utils.py
  - Added logging to `create_task()`
  - Added logging to `claim_task()` with attempt counting (both DB and file-based modes)
  - Added logging to `submit_completion()`
  - Added logging to `accept_completion()` with PR number extraction
  - Added logging to `reject_completion()`
  - Added logging to `fail_task()`
  - Added logging to `escalate_to_planning()`
  - Added logging to `recycle_to_breakdown()`

- [x] 3. Wire TaskLogger into review operations
  - All review operations are in queue_utils.py and have been wired up

- [x] 4. Enhance task-status.sh script
  - Created `scripts/task-log-info.py` helper script to extract claim info
  - Added --verbose flag to task-status.sh
  - Display claim count, first claim time, last claim time in verbose mode
  - Display file path in verbose mode
  - Display path to task log file in verbose mode

- [x] 5. Add tests for TaskLogger
  - Created `tests/test_task_logger.py` with comprehensive tests
  - Tests log file creation and formatting
  - Tests all log_* methods
  - Tests reading logs for claim counting and time extraction
  - Tests complete task lifecycle

- [x] 6. Update DB schema (if needed)
  - Decided NOT to add claim_count column to DB
  - Task logs provide the authoritative record and are more flexible

- [x] 7. Manual testing and verification
  - Created and ran end-to-end test script
  - Verified log creation, claim tracking, and time extraction
  - All syntax checks passed

## Expected Files to Modify/Create

**New files:**
- `orchestrator/task_logger.py` - Core logging infrastructure
- `tests/test_task_logger.py` - Unit tests for TaskLogger

**Modified files:**
- `orchestrator/queue_utils.py` - Wire in logging to queue operations
- `orchestrator/review_orch.py` - Wire in logging to review operations
- `scripts/task-status.sh` - Enhance with claim history and file paths
- `orchestrator/db.py` - Possibly add claim_count column (migration v13)

**Directories to create:**
- `.orchestrator/logs/` - Log directory root
- `.orchestrator/logs/tasks/` - Per-task log files

## Progress Log

**2026-02-11 Initial Analysis:**
- Reviewed task requirements from GitHub issue #3
- Examined existing queue_utils.py and db.py structure
- Confirmed TaskLogger doesn't exist yet
- Identified integration points in queue operations

**2026-02-11 Implementation:**
- Created TaskLogger module with all event logging functions
- Wired logging into all queue operations (create, claim, submit, accept, reject, fail, escalate, recycle)
- Enhanced task-status.sh with --verbose flag showing claim history
- Created task-log-info.py helper script
- Added comprehensive unit tests
- Verified with end-to-end test - all passing

**Implementation Complete:**
All acceptance criteria met:
- ✓ Per-task log files in `.orchestrator/logs/tasks/TASK-<id>.log`
- ✓ Logs track all state transitions (CREATED, CLAIMED, SUBMITTED, REJECTED, ACCEPTED, FAILED, ESCALATED, RECYCLED)
- ✓ Enhanced status script shows claim history with --verbose flag
- ✓ Helper functions to count claims and extract timestamps
- ✓ Comprehensive tests covering all functionality
