# Octopoid

A distributed AI orchestrator for software development. Queue work locally, execute on VMs, coordinate across multiple machines.

## What is Octopoid?

Octopoid manages autonomous Claude Code agents that work on tasks in parallel. It supports two versions:

- **v2.0 (New)** - TypeScript client-server architecture with offline mode, distributed orchestration, and SDKs
- **v1.x (Legacy)** - Python orchestrator as git submodule

This README covers **v2.0**. For v1.x documentation, see [README-v1.md](./README-v1.md).

## v2.0 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Central Server                            │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  REST API (Hono + Cloudflare Workers)                  │ │
│  │  - Task operations (claim, submit, accept, reject)     │ │
│  │  - Orchestrator registration & heartbeat               │ │
│  │  - State machine enforcement                           │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  D1 Database (SQLite at the edge)                      │ │
│  │  - Tasks, Projects, Agents, Orchestrators              │ │
│  │  - Task history and audit trail                        │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │ HTTPS/REST
            ┌───────────────┴───────────────┐
            │                               │
┌───────────▼──────────┐        ┌──────────▼───────────┐
│  Orchestrator Client │        │  Orchestrator Client  │
│  (Machine 1)         │        │  (Machine 2)          │
│  - Scheduler         │        │  - Scheduler          │
│  - Agent Roles       │        │  - Agent Roles        │
│  - Git Worktrees     │        │  - Git Worktrees      │
└─────────────────────┘        └──────────────────────┘
```

## Quick Start (v2.0)

### Option 1: Local Mode (No Server)

```bash
# Install dependencies
npm install -g pnpm
pnpm install

# Build packages
cd packages/shared && pnpm build && cd ../..
cd packages/client && pnpm build && cd ../..

# Link client globally
cd packages/client && npm link && cd ../..

# Initialize Octopoid
octopoid init --local

# Start orchestrator
octopoid start
```

### Option 2: Client-Server Mode

**Server (Cloudflare Workers):**

```bash
cd packages/server

# Install wrangler
npm install -g wrangler

# Create database
wrangler d1 create octopoid-db

# Run migrations
wrangler d1 migrations apply octopoid-db

# Deploy to Cloudflare
wrangler deploy

# Or run locally
wrangler dev
```

**Client:**

```bash
cd packages/client

# Build and link
pnpm build
npm link

# Initialize with server URL
octopoid init \
  --server https://octopoid-server.username.workers.dev \
  --cluster prod \
  --machine-id my-machine

# Start orchestrator
octopoid start --debug
```

### Automated Setup

We provide a setup script for local development:

```bash
./setup-dev.sh
```

This installs dependencies and builds all packages.

## Use Cases

### 1. Queue Locally, Execute on VM

```bash
# On laptop: Create tasks
octopoid enqueue "Add user authentication" --role implement --priority P1

# On VM with GPU: Run orchestrator
ssh vm
octopoid start --daemon
```

### 2. Multiple Computers Without Conflicts

```bash
# Mac Studio
octopoid init --cluster home --machine-id mac-studio
octopoid start

# Linux Workstation (same cluster)
octopoid init --cluster home --machine-id linux-ws
octopoid start

# Server coordinates - no conflicts!
```

### 3. Offline Mode

Works even when server is down:

```bash
octopoid enqueue "Fix bug" --role implement
# ✓ Task created locally, will sync when server available

octopoid status
# ⚠️  Working offline
# Pending sync: 3 operations
```

## CLI Commands

```bash
# Initialize project
octopoid init [--server URL] [--cluster NAME] [--local]

# Start orchestrator
octopoid start [--daemon] [--debug] [--once]

# Stop orchestrator
octopoid stop

# Check status
octopoid status

# Create task
octopoid enqueue <description> [--role ROLE] [--priority P0-P3]

# List tasks
octopoid list [--queue QUEUE] [--priority PRIORITY] [--role ROLE]

# Validate setup
octopoid validate
```

## Configuration

After running `octopoid init`, configuration is stored in `.octopoid/config.yaml`:

```yaml
# Mode: local or remote
mode: remote

# Server configuration (remote mode)
server:
  enabled: true
  url: https://octopoid-server.username.workers.dev
  cluster: production
  machine_id: my-machine
  api_key: your-api-key  # Optional

# Agent configuration
agents:
  max_concurrent: 3

# Repository
repo:
  path: /path/to/project
  main_branch: main
```

### Agent Definitions

Configure agents in `.octopoid/agents.yaml`:

```yaml
agents:
  - name: implementer-1
    role: implement
    interval_seconds: 300
    model: claude-sonnet-4-20250514
    max_turns: 50

  - name: breakdown-agent
    role: breakdown
    interval_seconds: 600
    model: claude-sonnet-4-20250514

  - name: gatekeeper-1
    role: review
    interval_seconds: 300
    model: claude-opus-4-20250514
```

## Custom Scripts (SDKs)

### Node.js/TypeScript SDK

```bash
npm install octopoid-sdk
```

```typescript
import { OctopoidSDK } from 'octopoid-sdk'

const sdk = new OctopoidSDK({
  serverUrl: 'https://octopoid-server.username.workers.dev',
  apiKey: process.env.OCTOPOID_API_KEY
})

// Create a task
const task = await sdk.tasks.create({
  id: 'my-task-123',
  file_path: 'tasks/incoming/TASK-my-task-123.md',
  queue: 'incoming',
  priority: 'P1',
  role: 'implement'
})

// List tasks
const tasks = await sdk.tasks.list({ queue: 'incoming' })

// Auto-approve tasks
for (const task of tasks) {
  if (task.role === 'docs' && task.commits_count > 0) {
    await sdk.tasks.accept(task.id, { accepted_by: 'auto-approve' })
  }
}
```

See [packages/sdk/README.md](./packages/sdk/README.md) for full API reference.

### Python SDK

```bash
pip install octopoid-sdk
```

```python
from octopoid_sdk import OctopoidSDK

sdk = OctopoidSDK(
    server_url='https://octopoid-server.username.workers.dev',
    api_key='your-api-key'
)

# Create a task
task = sdk.tasks.create(
    id='my-task-123',
    file_path='tasks/incoming/TASK-my-task-123.md',
    queue='incoming',
    priority='P1',
    role='implement'
)

# List tasks
tasks = sdk.tasks.list(queue='incoming')

# Auto-approve tasks
for task in tasks:
    if task['role'] == 'docs' and task.get('commits_count', 0) > 0:
        sdk.tasks.accept(task['id'], accepted_by='auto-approve')
```

See [packages/python-sdk/README.md](./packages/python-sdk/README.md) for full API reference.

## Architecture

### Packages

```
packages/
├── shared/          # TypeScript types (Task, Project, Orchestrator)
├── server/          # Cloudflare Workers server with D1 database
├── client/          # CLI + library (scheduler, agents, git ops)
├── sdk/             # Node.js/TypeScript SDK for custom scripts
└── python-sdk/      # Python SDK for custom scripts
```

### Task Lifecycle

```
incoming → claimed → provisional → done
    ↑         │           │
    └─────────┴───────────┘
       (reject/retry)
```

Tasks are:
- **Claimed** by agents (with lease expiration)
- **Submitted** to provisional queue after implementation
- **Accepted** into done queue after review
- **Rejected** back to incoming for retry

### State Machine

All state transitions are validated server-side:

- **Guards**: Check preconditions (dependencies resolved, role matches)
- **Side Effects**: Record history, unblock dependents, update leases
- **Optimistic Locking**: Version checking prevents conflicts

### Offline Mode

When server is unreachable:
- Operations saved to local SQLite cache
- Background sync manager retries every 30 seconds
- Transparent fallback with user notifications
- Conflict resolution (server state wins)

## Deployment

### Server (Cloudflare Workers)

```bash
cd packages/server

# Create D1 database
wrangler d1 create octopoid-db

# Copy database ID to wrangler.toml
# database_id = "your-database-id"

# Run migrations
wrangler d1 migrations apply octopoid-db

# Deploy
wrangler deploy
```

Cost: **Free tier includes 100k requests/day**

### Client (npm)

```bash
cd packages/client

# Build
pnpm build

# Publish to npm
npm publish

# Users install globally
npm install -g octopoid
```

## Monitoring

### Server Health

```bash
curl https://octopoid-server.username.workers.dev/api/health
```

### Client Status

```bash
octopoid status
```

Shows:
- Server connection status
- Online/offline mode
- Pending sync operations
- Task counts by queue
- Orchestrator info

## Migration from v1.x

If you're using the Python orchestrator (v1.x):

1. **Export current state**:
   ```bash
   python orchestrator/scripts/export_state.py --output backup.json
   ```

2. **Deploy v2.0 server** (see Deployment section)

3. **Import state to server**:
   ```bash
   curl -X POST https://your-server/api/admin/import \
     -H "Content-Type: application/json" \
     -d @backup.json
   ```

4. **Install v2.0 client**:
   ```bash
   npm install -g octopoid
   octopoid init --server https://your-server --cluster prod
   ```

5. **Test in parallel** before switching fully

See [docs/migration-v2.md](./docs/migration-v2.md) for detailed migration guide.

## Documentation

- [Architecture](./docs/architecture-v2.md) - System design and components
- [Quick Start](./docs/quickstart-v2.md) - 5-minute setup guide
- [Migration](./docs/migration-v2.md) - Migrate from v1.x to v2.0
- [Deployment](./docs/deployment.md) - Production deployment guide
- [API Reference](./packages/server/README.md) - REST API documentation
- [SDK Guide](./packages/sdk/README.md) - Node.js SDK reference
- [Python SDK Guide](./packages/python-sdk/README.md) - Python SDK reference

## Development

```bash
# Install dependencies
pnpm install

# Build all packages
pnpm build

# Run tests
pnpm test

# Run server locally
cd packages/server && wrangler dev

# Run client in debug mode
octopoid start --debug --once
```

## License

MIT

## Contributing

Contributions welcome! See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

---

**v1.x Documentation:** For the legacy Python orchestrator, see [README-v1.md](./README-v1.md)
