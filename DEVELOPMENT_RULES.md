# Development Rules

Guidelines for maintaining the Octopoid codebase.

## Architecture

### Rule: v2.0 is API-Only

**In v2.0, all task state operations must go through the API. No database mode, no file-based queue mode.**

v2.0 is a client-server architecture:
- **Server**: Cloudflare Workers with D1 database (single source of truth)
- **Clients**: Use SDK to communicate with server
- **No file-based queues**: Queue state is not managed via files

**What uses API:**
- Task lifecycle (claim, submit, complete, reject, fail)
- Task queries (list, get, count)
- Task creation and deletion
- All task state changes

**What stays local:**
- Task markdown files (`.octopoid/queue/*.md`) - for content/context
- Worktree markers (`.octopoid/runtime/*`) - local worktree state
- Git operations (branches, commits, worktrees)
- Agent logs

**Bad (v1.x patterns):**
```python
# Move file from incoming/ to claimed/
task_file.rename(claimed_dir / task_file.name)
```

**Good (v2.0 pattern):**
```python
from octopoid_sdk import OctopoidSDK
sdk = OctopoidSDK(server_url=config.server.url)
task = sdk.tasks.claim(orchestrator_id=..., agent_name=...)
```

**Migration checklist:**
- [ ] Remove file-based queue operations (moving files between queue dirs)
- [ ] Replace with SDK calls
- [ ] Keep only local file reading (parse_task_file) and worktree markers

**Rationale:**
- Single source of truth (server)
- Enables distributed orchestrators
- Consistent state across all clients
- No file/database sync issues
- Proper audit trail in server

## Testing

### Rule: Use Separate Test Instance

**Never test against production data or the main development server.**

When testing new features or debugging:
1. Use `--demo` mode for the dashboard
2. Use a separate test server instance

**Bad:**
```bash
# Testing directly against main dev server
python3 test-script.py --server http://localhost:8787
```

**Good:**
```bash
# Spin up isolated test server
cd packages/server
wrangler dev --port 8788  # Different port

# Test against isolated instance
python3 test-script.py --server http://localhost:8788
```

## API Operations

### Rule: Use API Endpoints and Scripts

**Always prefer creating endpoints and scripts over direct data manipulation.**

When you need to modify tasks or other data:

1. **First choice:** Create/use an API endpoint
2. **Second choice:** Create a script that uses the SDK/API

**Rationale:**
- API endpoints are versioned and documented
- Scripts can be reused and tested

### Creating New Endpoints

When you need a new operation:

1. Add the endpoint to `packages/server/src/routes/*.ts`
2. Add the method to `packages/python-sdk/octopoid_sdk/client.py`
3. Create a script in `scripts/` that demonstrates usage
4. Update the endpoint list in `packages/server/src/index.ts`
5. Document in CHANGELOG.md

**Example:**
```typescript
// packages/server/src/routes/tasks.ts
tasksRoute.delete('/:id', async (c) => {
  const db = c.env.DB
  const taskId = c.req.param('id')
  await execute(db, 'DELETE FROM tasks WHERE id = ?', taskId)
  return c.json({ message: 'Task deleted', task_id: taskId })
})
```

```python
# packages/python-sdk/octopoid_sdk/client.py
def delete(self, task_id: str) -> Dict[str, Any]:
    """Delete a task"""
    return self.client._request('DELETE', f'/api/v1/tasks/{task_id}')
```

## Documentation

### Rule: Update CHANGELOG.md

**Every user-facing change must be documented in CHANGELOG.md**

When you make changes:
1. Add entry to `## [Unreleased]` section
2. Use appropriate category: Added, Changed, Deprecated, Removed, Fixed, Security
3. Keep entries concise and user-focused
4. Focus on WHAT changed, not HOW

**Format:**
```markdown
### Added
- DELETE endpoint for tasks (cleanup test data)
- Cleanup script for removing test data

### Changed
- Task creation now requires title field

### Fixed
- Dashboard shows task titles instead of IDs
```

## Git Workflow

### Commit Messages

Use conventional commit format:
```
<type>: <description>

<optional body>

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

### Before Pushing

1. Run cleanup script to remove test data
2. Update CHANGELOG.md
3. Stage only relevant files (no runtime data, no test artifacts)
4. Create clear commit message
5. Push to feature branch

## File Organization

### Don't Commit Runtime Data

Files to exclude:
- `.octopoid/queue/` - task queue files
- `.octopoid/logs/` - log files
- `.octopoid/runtime/` - PIDs, state files
- `.octopoid/worktrees/` - git worktrees
- `*.backup` - backup files

These are already in `.gitignore`, but be careful when staging files.

## Scripts

### Location and Naming

- Place reusable scripts in `scripts/`
- Use descriptive names: `cleanup-test-data.py`, not `cleanup.py`
- Make scripts executable: `chmod +x scripts/*.py`
- Add usage documentation in docstring
- Support `--help` flag

### Script Template

```python
#!/usr/bin/env python3
"""
Brief description of what the script does.

Usage:
    python scripts/script-name.py --server <url> [options]
"""

import argparse
import sys
from octopoid_sdk import OctopoidSDK

def main():
    parser = argparse.ArgumentParser(description='...')
    parser.add_argument('--server', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    sdk = OctopoidSDK(server_url=args.server)

    # Do work

    return 0

if __name__ == '__main__':
    sys.exit(main())
```

## Summary

1. ✅ Use separate test instances
2. ✅ Create endpoints and scripts for data operations
3. ✅ Update CHANGELOG.md for all changes
4. ✅ Write clear commit messages
5. ✅ Don't commit runtime data or test artifacts
