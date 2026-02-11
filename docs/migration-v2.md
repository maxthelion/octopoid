# Migration Guide: v1.x → v2.0

This guide provides step-by-step instructions for migrating from the Python-based Octopoid v1.x to the Node.js/TypeScript client-server architecture in v2.0.

## Overview

**v1.x (Python):**
- Git submodule for orchestrator code
- Direct SQLite database access
- Single-machine operation
- Python virtual environment

**v2.0 (Node.js/TypeScript):**
- npm package distribution
- Client-server architecture
- Distributed orchestration
- Cloudflare Workers server

## Prerequisites

- Node.js >= 18.0.0
- pnpm >= 8.0.0 (or npm)
- Cloudflare account (for server deployment)
- Existing Octopoid v1.x installation

## Migration Steps

### Step 1: Backup Current State

**Export database:**
```bash
# Create backup of SQLite database
cp .orchestrator/state.db .orchestrator/state.db.backup

# Export to JSON (script to be created)
python orchestrator/scripts/export_state.py --output backup-$(date +%Y%m%d).json
```

**Backup custom scripts:**
```bash
# If you have custom scripts
cp -r .orchestrator/scripts .orchestrator/scripts.backup
```

### Step 2: Deploy Server

**Option A: Cloudflare Workers (Recommended)**

```bash
# Clone server repo or navigate to packages/server
cd packages/server

# Install dependencies
pnpm install

# Create D1 database
pnpm db:create

# Copy the database_id output and update wrangler.toml:
# [[d1_databases]]
# binding = "DB"
# database_name = "octopoid-db"
# database_id = "YOUR_DATABASE_ID_HERE"

# Apply migrations
pnpm db:migrations:apply

# Deploy
pnpm deploy

# Note the deployed URL, e.g.:
# https://octopoid-server.your-username.workers.dev
```

**Option B: Self-hosted (Advanced)**

See [self-hosting.md](./self-hosting.md) for Docker deployment instructions.

### Step 3: Import State to Server

```bash
# Import backup to server
curl -X POST https://octopoid-server.your-username.workers.dev/api/admin/import \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d @backup-20260211.json

# Verify import
curl https://octopoid-server.your-username.workers.dev/api/v1/tasks | jq length
# Should show your task count
```

### Step 4: Install New Client

```bash
# Install client globally
npm install -g octopoid

# Verify installation
octopoid --version
# Should show: 2.0.0
```

### Step 5: Initialize New Client (Test Mode)

```bash
# Initialize in test mode (won't interfere with v1.x)
mkdir ../test-octopoid-v2
cd ../test-octopoid-v2
git init

octopoid init \
  --server https://octopoid-server.your-username.workers.dev \
  --cluster production \
  --machine-id test-001

# Verify connection
octopoid status
# Should show: ✓ Connected to server
```

### Step 6: Test Parallel Operation

Run both v1.x and v2.0 side-by-side to verify:

```bash
# Terminal 1: v1.x orchestrator (if running)
cd /path/to/original/project
# Check it's working
.orchestrator/scripts/status.py

# Terminal 2: v2.0 client status
cd /path/to/test-octopoid-v2
octopoid status

# Create test task via v2.0
octopoid enqueue "Test migration task" --role implement --priority P3

# Verify task appears in server
octopoid list --queue incoming
```

### Step 7: Stop Old Orchestrator

```bash
# Find and stop scheduler process
pkill -f "python.*scheduler.py"

# Verify no agents running
ps aux | grep -E "(implementer|breakdown|gatekeeper)" | grep -v grep

# Should show no results
```

### Step 8: Remove Git Submodule

```bash
cd /path/to/original/project

# Deinitialize submodule
git submodule deinit orchestrator

# Remove from git
git rm orchestrator
rm -rf .git/modules/orchestrator

# Commit removal
git add .gitmodules
git commit -m "Remove orchestrator submodule, migrated to octopoid v2.0"
```

### Step 9: Initialize v2.0 in Production

```bash
cd /path/to/original/project

# Initialize v2.0
octopoid init \
  --server https://octopoid-server.your-username.workers.dev \
  --cluster production \
  --machine-id $(hostname)

# Review configuration
cat .octopoid/config.yaml

# Customize agents if needed
vim .octopoid/agents.yaml
```

### Step 10: Start New Orchestrator

**On local machine:**
```bash
octopoid start --daemon

# Check status
octopoid status
# Should show: ✓ Running
```

**On VM (for heavy workloads):**
```bash
ssh your-vm

# Install Node.js if needed
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install octopoid
npm install -g octopoid

# Initialize
octopoid init \
  --server https://octopoid-server.your-username.workers.dev \
  --cluster production \
  --machine-id vm-gpu-001

# Start
octopoid start --daemon

# Verify
octopoid status
```

### Step 11: Migrate Custom Scripts

If you have custom scripts in `.orchestrator/scripts/`:

**Option A: Rewrite using Python SDK**
```python
# .octopoid/scripts/custom_status.py
from octopoid_sdk import OctopoidClient

client = OctopoidClient(
    server_url="https://octopoid-server.your-username.workers.dev",
    api_key=os.getenv("OCTOPOID_API_KEY")
)

tasks = client.list_tasks(queue='incoming')
for task in tasks:
    print(f"{task['id']}: {task['priority']}")
```

**Option B: Rewrite using Node.js SDK**
```typescript
// .octopoid/scripts/custom-status.ts
import { OctopoidClient } from 'octopoid-sdk'

const client = new OctopoidClient({
  serverUrl: 'https://octopoid-server.your-username.workers.dev',
  apiKey: process.env.OCTOPOID_API_KEY
})

const tasks = await client.listTasks({ queue: 'incoming' })
for (const task of tasks) {
  console.log(`${task.id}: ${task.priority}`)
}
```

**Option C: Use REST API directly**
```bash
# .octopoid/scripts/custom-status.sh
SERVER="https://octopoid-server.your-username.workers.dev"
API_KEY="${OCTOPOID_API_KEY}"

curl -s "${SERVER}/api/v1/tasks?queue=incoming" \
  -H "Authorization: Bearer ${API_KEY}" \
  | jq -r '.tasks[] | "\(.id): \(.priority)"'
```

### Step 12: Verify and Cleanup

**Verification checklist:**
- [ ] All tasks visible via `octopoid list`
- [ ] Orchestrator running: `octopoid status`
- [ ] Tasks being claimed and worked
- [ ] Custom scripts migrated and working
- [ ] Team members can access server

**Cleanup old files:**
```bash
# After 1 week of successful operation
rm -rf .orchestrator/
rm -rf .orchestrator.backup/
rm backup-*.json
```

## Rollback Procedure

If you need to rollback to v1.x:

```bash
# Stop v2.0 client
npm uninstall -g octopoid

# Restore git submodule
git checkout orchestrator
git submodule update --init --recursive

# Restore database
cp .orchestrator/state.db.backup .orchestrator/state.db

# Restore scripts
cp -r .orchestrator/scripts.backup .orchestrator/scripts

# Restart v1.x orchestrator
cd orchestrator
./venv/bin/python orchestrator/scheduler.py
```

## Troubleshooting

### "Cannot connect to server"
- Verify server URL: `curl https://your-server/api/health`
- Check network/VPN connectivity
- Verify Cloudflare Workers is deployed

### "Task already exists"
- Old task IDs may conflict
- Use `octopoid list` to see existing tasks
- Create new tasks with new IDs

### "API version mismatch"
- Update client: `npm update -g octopoid`
- Or update server: `cd packages/server && pnpm deploy`

### Custom scripts not working
- Check Python SDK installation: `pip install octopoid-sdk`
- Verify API key is set: `echo $OCTOPOID_API_KEY`
- Test REST API directly with curl

## Support

- GitHub Issues: https://github.com/org/octopoid/issues
- Documentation: https://docs.octopoid.dev
- Migration FAQ: https://docs.octopoid.dev/migration-faq

## Timeline

Expected migration time:
- **Preparation:** 30 minutes (backup, deploy server)
- **Testing:** 1-2 hours (parallel operation)
- **Production cutover:** 30 minutes (stop old, start new)
- **Custom script migration:** 1-4 hours (depends on number of scripts)

**Total:** 2-7 hours depending on customization level
