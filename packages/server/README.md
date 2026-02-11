# @octopoid/server

Octopoid server - Cloudflare Workers API for distributed orchestration.

## Overview

This package provides the central REST API server for Octopoid's client-server architecture. It runs on Cloudflare Workers and uses D1 (SQLite at the edge) for state management.

## Features

- **REST API** - Hono-based API for task and orchestrator management
- **D1 Database** - SQLite database at the edge with global replication
- **State Machine** - Enforces valid state transitions for tasks
- **Lease-based Claiming** - Atomic task claiming with automatic expiration
- **Scheduled Jobs** - Background tasks for lease monitoring
- **Edge Deployment** - Runs globally on Cloudflare's edge network

## Prerequisites

- Node.js >= 18.0.0
- pnpm >= 8.0.0
- Cloudflare account
- Wrangler CLI (installed via pnpm)

## Development

### Local Development Server

```bash
# Install dependencies
pnpm install

# Start local development server
pnpm dev

# Server will be available at http://localhost:8787
```

### Database Setup

```bash
# Create D1 database
pnpm db:create

# This will output a database ID. Copy it to wrangler.toml:
# [[d1_databases]]
# binding = "DB"
# database_name = "octopoid-db"
# database_id = "YOUR_DATABASE_ID_HERE"

# Apply migrations
pnpm db:migrations:apply
```

### Create New Migration

```bash
# Create a new migration file
pnpm db:migrations:create my_migration_name

# Edit the generated file in migrations/
# Then apply it:
pnpm db:migrations:apply
```

## Deployment

### Deploy to Cloudflare Workers

```bash
# Build and deploy
pnpm deploy

# Output will show your worker URL:
# https://octopoid-server.your-username.workers.dev
```

### Environment Variables

Set secrets using Wrangler:

```bash
# API secret key (for admin endpoints)
wrangler secret put API_SECRET_KEY

# Anthropic API key (optional, for future server-side features)
wrangler secret put ANTHROPIC_API_KEY
```

### Custom Domain

To use a custom domain (e.g., `octopoid.example.com`):

1. Add domain to Cloudflare
2. Uncomment the `routes` section in `wrangler.toml`
3. Update with your domain
4. Deploy again: `pnpm deploy`

## API Endpoints

### Health Check
```bash
GET /api/health

# Response:
{
  "status": "healthy",
  "version": "2.0.0",
  "timestamp": "2024-02-11T10:00:00Z",
  "database": "connected"
}
```

### Orchestrators
```bash
# Register orchestrator
POST /api/v1/orchestrators/register

# Heartbeat
POST /api/v1/orchestrators/:id/heartbeat

# List orchestrators
GET /api/v1/orchestrators
```

### Tasks
```bash
# List tasks
GET /api/v1/tasks?queue=incoming&priority=P1

# Get task
GET /api/v1/tasks/:id

# Create task
POST /api/v1/tasks

# Claim task
POST /api/v1/tasks/claim

# Submit completion
POST /api/v1/tasks/:id/submit

# Accept task
POST /api/v1/tasks/:id/accept

# Reject task
POST /api/v1/tasks/:id/reject
```

See [API documentation](../../docs/api-reference.md) for detailed request/response schemas.

## Architecture

```
┌─────────────────────────────────────────┐
│   Cloudflare Workers (Edge Network)     │
│  ┌────────────────────────────────────┐ │
│  │  Hono Application                  │ │
│  │  - REST API routes                 │ │
│  │  - State machine logic             │ │
│  │  - Request validation              │ │
│  └────────────────────────────────────┘ │
│  ┌────────────────────────────────────┐ │
│  │  D1 Database (SQLite)              │ │
│  │  - Global replication              │ │
│  │  - Automatic backups               │ │
│  └────────────────────────────────────┘ │
│  ┌────────────────────────────────────┐ │
│  │  Cron Triggers                     │ │
│  │  - Lease expiration (every minute) │ │
│  │  - Stale orchestrator cleanup      │ │
│  └────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

## Testing

```bash
# Run tests
pnpm test

# Run tests with coverage
pnpm test:coverage

# Type checking
pnpm typecheck
```

## Monitoring

Cloudflare provides built-in monitoring:
- **Analytics** - Request counts, latency, errors
- **Logs** - Real-time logs via `wrangler tail`
- **Alerts** - Set up alerts for errors or high latency

```bash
# View real-time logs
wrangler tail

# View analytics
wrangler dashboard
```

## Cost

Cloudflare Workers Free Tier:
- **Requests:** 100,000/day
- **CPU time:** 10ms per request
- **D1 database:** 10GB storage, 5M reads/day, 100K writes/day

This is sufficient for small to medium teams. Paid plans available for higher limits.

## Status

**Current Phase:** Foundation setup ✅

- [x] Basic Hono app structure
- [x] Database schema (D1 migrations)
- [x] Health check endpoint
- [x] Configuration management
- [ ] State machine implementation
- [ ] Task routes
- [ ] Orchestrator routes
- [ ] Scheduled jobs
- [ ] Tests

## License

MIT
