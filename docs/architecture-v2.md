# Octopoid v2.0 - Architecture Documentation

## Overview

Octopoid v2.0 transforms from a single-machine Python orchestrator to a distributed client-server architecture using Node.js/TypeScript and Cloudflare Workers.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Central Server                            │
│                (Cloudflare Workers + D1)                     │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  REST API (Hono framework)                              │ │
│  │  - Task operations (claim, submit, accept, reject)     │ │
│  │  - Orchestrator registration & heartbeat               │ │
│  │  - State machine enforcement                           │ │
│  │  - Optimistic locking (version-based)                  │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  D1 Database (SQLite at the edge)                      │ │
│  │  - Tasks, Projects, Agents, Orchestrators              │ │
│  │  - Lease-based claim tracking                          │ │
│  │  - Task history and audit trail                        │ │
│  │  - Global replication                                  │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Scheduled Jobs (Cron triggers)                        │ │
│  │  - Lease expiration monitoring (every minute)          │ │
│  │  - Stale orchestrator cleanup                          │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │ HTTPS/REST
            ┌───────────────┴───────────────┐
            │                               │
┌───────────▼──────────┐        ┌──────────▼───────────┐
│  Orchestrator Client │        │  Orchestrator Client  │
│  (Machine 1)         │        │  (Machine 2)          │
│                      │        │                       │
│  ┌────────────────┐ │        │  ┌────────────────┐  │
│  │ CLI            │ │        │  │ CLI            │  │
│  │ - init         │ │        │  │ - init         │  │
│  │ - start/stop   │ │        │  │ - start/stop   │  │
│  │ - enqueue      │ │        │  │ - enqueue      │  │
│  │ - status/list  │ │        │  │ - status/list  │  │
│  └────────────────┘ │        │  └────────────────┘  │
│  ┌────────────────┐ │        │  ┌────────────────┐  │
│  │ API Client     │ │        │  │ API Client     │  │
│  │ - HTTP wrapper │ │        │  │ - HTTP wrapper │  │
│  │ - Type-safe    │ │        │  │ - Type-safe    │  │
│  └────────────────┘ │        │  └────────────────┘  │
│  ┌────────────────┐ │        │  ┌────────────────┐  │
│  │ Scheduler      │ │        │  │ Scheduler      │  │
│  │ - Claim tasks  │ │        │  │ - Claim tasks  │  │
│  │ - Spawn agents │ │        │  │ - Spawn agents │  │
│  │ - Heartbeat    │ │        │  │ - Heartbeat    │  │
│  └────────────────┘ │        │  └────────────────┘  │
│  ┌────────────────┐ │        │  ┌────────────────┐  │
│  │ Agent Roles    │ │        │  │ Agent Roles    │  │
│  │ - Implementer  │ │        │  │ - Implementer  │  │
│  │ - Breakdown    │ │        │  │ - Breakdown    │  │
│  │ - Gatekeeper   │ │        │  │ - Gatekeeper   │  │
│  └────────────────┘ │        │  └────────────────┘  │
│  ┌────────────────┐ │        │  ┌────────────────┐  │
│  │ Local Files    │ │        │  │ Local Files    │  │
│  │ - Task .md     │ │        │  │ - Task .md     │  │
│  │ - Git worktrees│ │        │  │ - Git worktrees│  │
│  └────────────────┘ │        │  └────────────────┘  │
└─────────────────────┘        └──────────────────────┘
```

## Key Design Principles

### 1. Server Stores State, Clients Store Files

- **Server (D1 database):** Task metadata, state, claims, leases
- **Clients (local filesystem):** Task markdown files, git repositories, worktrees
- **No file transfers via API:** Files sync via git, not HTTP

### 2. Orchestrators Run Locally

- Scheduler and agents run on client machines (or VMs)
- Server is stateless API layer
- Benefits: GPU access, local git operations, filesystem access

### 3. API-Mediated Coordination

- All state changes go through server API
- State machine enforces valid transitions
- Optimistic locking prevents conflicts

### 4. Lease-Based Claiming

- Tasks claimed with expiration time (default: 5 minutes)
- Automatic release on timeout
- Prevents double-claiming and stuck tasks

### 5. Git as File Transport

- Task files committed to git repository
- Orchestrators pull to get new tasks
- Push to share results
- Server only stores metadata

## Components

### Server (@octopoid/server)

**Technology:**
- Hono framework (fast web framework)
- Cloudflare Workers (serverless edge compute)
- D1 database (SQLite at the edge)

**Responsibilities:**
- Enforce state machine transitions
- Manage task claims and leases
- Store task metadata
- Track orchestrator presence (heartbeat)
- Release expired leases (cron job)

**Endpoints:**
```
GET  /api/health
POST /api/v1/orchestrators/register
POST /api/v1/orchestrators/:id/heartbeat
GET  /api/v1/orchestrators
GET  /api/v1/tasks
POST /api/v1/tasks
POST /api/v1/tasks/claim
POST /api/v1/tasks/:id/submit
POST /api/v1/tasks/:id/accept
POST /api/v1/tasks/:id/reject
```

### Client (octopoid)

**Technology:**
- Node.js/TypeScript
- Commander (CLI framework)
- Anthropic SDK (AI integration)
- simple-git (git operations)

**Responsibilities:**
- Provide CLI interface
- Claim tasks from server
- Spawn and manage agents
- Execute git operations locally
- Maintain task files

**CLI Commands:**
```bash
octopoid init           # Initialize project
octopoid start          # Start orchestrator
octopoid stop           # Stop orchestrator
octopoid status         # Show status
octopoid enqueue        # Create task
octopoid list           # List tasks
octopoid validate       # Validate config
```

### Shared Types (@octopoid/shared)

**Exports:**
- Task, Project, Agent, Orchestrator types
- Request/response types for API
- State machine types

**Ensures:**
- Type consistency across server and client
- Compile-time validation
- API contract enforcement

## Data Flow

### Task Creation

```
User → CLI (octopoid enqueue)
     → API Client (POST /api/v1/tasks)
     → Server (validate, insert to DB)
     → Response (task ID)
     → User creates task.md file locally
     → Git commit and push
```

### Task Claiming

```
Orchestrator → API Client (POST /api/v1/tasks/claim)
             → Server (find available task, atomic claim with lease)
             → Response (task details)
             → Orchestrator creates git worktree
             → Agent spawns and works on task
```

### Task Submission

```
Agent → Git commit changes
      → Push to branch
      → API Client (POST /api/v1/tasks/:id/submit)
      → Server (transition: claimed → provisional)
      → Response (success)
      → Task awaits acceptance
```

### Task Acceptance

```
Reviewer → API Client (POST /api/v1/tasks/:id/accept)
         → Server (transition: provisional → done)
         → Server unblocks dependent tasks
         → Response (success)
```

## State Machine

### Task States

```
incoming → claimed → provisional → done
   ↓          ↓
blocked    (lease expired)
   ↓          ↓
incoming ← incoming
```

### Transitions

| Transition | From | To | Guards | Side Effects |
|------------|------|-----|--------|--------------|
| claim | incoming | claimed | dependency_resolved, role_matches | set_claimed_by, create_lease, record_history |
| submit | claimed | provisional | lease_valid, version_matches | record_history |
| accept | provisional | done | none | unblock_dependents, record_history |
| reject | provisional | incoming | none | increment_rejection_count, record_history |
| requeue | claimed | incoming | none | clear_claim, record_history |

### Guards

- **dependency_resolved:** Blocked task is done
- **role_matches:** Task role matches agent filter
- **lease_valid:** Lease hasn't expired
- **version_matches:** Optimistic lock check

### Side Effects

- **record_history:** Add audit trail entry
- **unblock_dependents:** Clear blocked_by for dependent tasks
- **update_lease:** Set lease expiration timestamp

## Deployment Models

### Personal (Single Developer)

```
Laptop (task creation) → Server (Cloudflare) → VM (GPU orchestrator)
```

- Developer creates tasks on laptop
- VM with GPU claims and works tasks
- Results visible on laptop after push

### Team (Multiple Developers)

```
Dev 1 Laptop ─┐
Dev 2 Laptop ─┼──> Server ──> Shared VM Pool
Dev 3 Laptop ─┘    (state)    (3x VMs, coordinated)
```

- All developers create tasks
- Multiple VMs claim tasks (no conflicts)
- Server coordinates via leases

### High-Scale (Large Organization)

```
Web Interface ─┐
Mobile App    ─┤
CLI Clients   ─┼──> Server ──> VM Pool (10+ VMs)
Integrations  ─┘    (HA)       (auto-scaling)
```

- Multiple task creation sources
- Large VM pool for parallel execution
- High-availability server deployment

## Security

### Authentication

**Phase 1 (v2.0):**
- API keys for server access
- Environment variable storage

**Phase 2 (future):**
- mTLS for client-server communication
- JWT tokens with expiration
- OAuth integration

### Authorization

- Cluster-based isolation
- Future: Role-based access control (RBAC)

### Data Protection

- HTTPS for all API calls
- D1 database encrypted at rest
- Secrets management via Cloudflare Workers secrets

## Scalability

### Server (Cloudflare Workers)

- **Auto-scaling:** Handles any request load
- **Global:** Edge network (200+ locations)
- **Limits:** 100k requests/day (free), unlimited (paid)

### Database (D1)

- **Storage:** 10GB (free), 100GB (paid)
- **Reads:** 5M/day (free), unlimited (paid)
- **Writes:** 100K/day (free), unlimited (paid)

### Clients

- **Horizontal:** Add more orchestrator VMs
- **Vertical:** Use larger VMs with more GPUs
- **Geographic:** Deploy VMs in multiple regions

## Monitoring

### Health Checks

```bash
# Server health
curl https://server/api/health
# {"status":"healthy","version":"2.0.0"}

# Client status
octopoid status
```

### Metrics (Future)

- Request counts and latency (Cloudflare Analytics)
- Task throughput (tasks claimed/submitted per hour)
- Agent utilization (active agents vs configured)
- Error rates (API errors, agent failures)

### Logging

- Server: Cloudflare Workers logs (`wrangler tail`)
- Client: `.octopoid/logs/` directory
- Audit trail: `task_history` table

## Comparison: v1.x vs v2.0

| Aspect | v1.x (Python) | v2.0 (Node.js/TypeScript) |
|--------|---------------|---------------------------|
| **Distribution** | Git submodule | npm package |
| **Architecture** | Single machine | Client-server |
| **Database** | Local SQLite | D1 (server) + optional local cache |
| **Coordination** | None (single orchestrator) | Lease-based (multiple orchestrators) |
| **Deployment** | Local only | Local + VM + cloud |
| **Installation** | git submodule update | npm install -g |
| **Updates** | git pull in submodule | npm update -g |
| **Language** | Python 3.11+ | Node.js 18+ |
| **Type Safety** | Type hints (runtime) | TypeScript (compile-time) |

## Future Enhancements

### Phase 2: Distributed Mode

- Multiple orchestrators competing for same task pool
- Advanced lease management (renewal, conflict resolution)
- Load balancing across orchestrators

### Phase 3: Advanced Features

- Web UI for task management
- Webhook notifications
- Metrics dashboard (Grafana)
- Rate limiting per orchestrator
- Task priorities with SLA tracking

### Phase 4: Integration

- GitHub Actions integration
- Slack/Discord bot
- JIRA/Linear bidirectional sync
- CI/CD pipeline integration
