# Octopoid v2.0 - Packages

This directory contains the Node.js/TypeScript implementation of Octopoid's client-server architecture.

## Package Structure

```
packages/
├── shared/          # Shared TypeScript types
├── server/          # Cloudflare Workers server (REST API)
└── client/          # CLI client and orchestrator
```

## Packages

### @octopoid/shared

Shared TypeScript type definitions used by both server and client:
- Task types
- Orchestrator types
- Project types
- API request/response types
- State machine types

### @octopoid/server

Cloudflare Workers server providing REST API for distributed orchestration:
- Hono-based REST API
- D1 (SQLite) database for state management
- State machine enforcement
- Lease-based task claiming
- Scheduled jobs (lease expiration monitoring)

**Deployment:**
```bash
cd packages/server
pnpm install
pnpm db:create              # Create D1 database
pnpm db:migrations:apply    # Run migrations
pnpm deploy                 # Deploy to Cloudflare Workers
```

### octopoid (client)

CLI client and local orchestrator:
- Command-line interface (init, start, stop, status, etc.)
- Local orchestrator (scheduler + agents)
- API client for server communication
- Offline mode with local cache
- Git operations and worktree management
- Agent roles (implementer, breakdown, gatekeeper, etc.)

**Installation:**
```bash
# Install globally
npm install -g octopoid

# Or build from source
cd packages/client
pnpm install
pnpm build
npm link
```

## Development

### Prerequisites

- Node.js >= 18.0.0
- pnpm >= 8.0.0
- Cloudflare account (for server deployment)

### Setup

```bash
# Install dependencies for all packages
pnpm install

# Build all packages
pnpm build

# Run tests
pnpm test

# Type checking
pnpm typecheck
```

### Development Workflow

**Server development:**
```bash
cd packages/server
pnpm dev                    # Start local dev server
```

**Client development:**
```bash
cd packages/client
pnpm dev -- init            # Run CLI commands in dev mode
pnpm watch                  # Watch for file changes
```

## Architecture

See [../docs/architecture-v2.md](../docs/architecture-v2.md) for detailed architecture documentation.

### High-Level Design

```
┌─────────────────────────────────────────────────────────────┐
│                    Central Server (Cloudflare Workers)       │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  REST API (Hono)                                        │ │
│  │  - Task operations (claim, submit, accept, reject)     │ │
│  │  - Orchestrator registration & heartbeat               │ │
│  │  - State machine enforcement                           │ │
│  └────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  D1 Database (SQLite at the edge)                      │ │
│  │  - Tasks, Projects, Agents, Orchestrators              │ │
│  │  - Lease-based claim tracking                          │ │
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
│                      │        │                       │
│  ┌────────────────┐ │        │  ┌────────────────┐  │
│  │ CLI Interface  │ │        │  │ CLI Interface  │  │
│  └────────────────┘ │        │  └────────────────┘  │
│  ┌────────────────┐ │        │  ┌────────────────┐  │
│  │ API Client     │ │        │  │ API Client     │  │
│  └────────────────┘ │        │  └────────────────┘  │
│  ┌────────────────┐ │        │  ┌────────────────┐  │
│  │ Scheduler      │ │        │  │ Scheduler      │  │
│  └────────────────┘ │        │  └────────────────┘  │
│  ┌────────────────┐ │        │  ┌────────────────┐  │
│  │ Agent Roles    │ │        │  │ Agent Roles    │  │
│  └────────────────┘ │        │  └────────────────┘  │
│  ┌────────────────┐ │        │  ┌────────────────┐  │
│  │ Local Files    │ │        │  │ Local Files    │  │
│  └────────────────┘ │        │  └────────────────┘  │
└─────────────────────┘        └──────────────────────┘
```

## Migration from v1.x

See [../docs/migration.md](../docs/migration.md) for detailed migration guide.

**Quick overview:**
1. Python orchestrator (git submodule) → npm package
2. Direct SQLite access → REST API with server coordination
3. Single machine → Distributed with multiple orchestrators
4. Local-only → Client-server with offline fallback

## Status

**Current Phase:** Phase 1 - Foundation setup ✅

**Completed:**
- [x] Monorepo structure
- [x] Package configuration
- [x] TypeScript setup
- [ ] Shared types
- [ ] Server foundation
- [ ] Client foundation
- [ ] Database schema
- [ ] API endpoints
- [ ] Agent roles ported
- [ ] CLI commands
- [ ] Tests
- [ ] Documentation

See [../docs/roadmap-v2.md](../docs/roadmap-v2.md) for full roadmap.
