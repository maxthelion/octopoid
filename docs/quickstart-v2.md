# Octopoid v2.0 - Quick Start Guide

Get started with Octopoid in under 5 minutes.

## Prerequisites

- Node.js >= 18.0.0
- Cloudflare account (for server deployment)
- Or: Access to an existing Octopoid server

## Option 1: Using Existing Server

If your team already has an Octopoid server deployed:

```bash
# Install client
npm install -g octopoid

# Initialize in your project
cd ~/projects/my-app
octopoid init \
  --server https://octopoid.example.com \
  --cluster production

# Create your first task
octopoid enqueue "Add user authentication" --role implement --priority P1

# Check status
octopoid status

# Start orchestrator (if not running on VM)
octopoid start --daemon
```

Done! The orchestrator will claim and work on your task automatically.

## Option 2: Deploy Your Own Server

### Step 1: Deploy Server (One-time setup)

```bash
# Clone repository
git clone https://github.com/org/octopoid.git
cd octopoid/packages/server

# Install dependencies
pnpm install

# Create database
pnpm db:create

# Output will show:
# database_id = "abc-123-def-456"

# Update wrangler.toml with database_id
nano wrangler.toml
# [[d1_databases]]
# binding = "DB"
# database_name = "octopoid-db"
# database_id = "abc-123-def-456"  # ← Paste your ID here

# Apply migrations
pnpm db:migrations:apply

# Deploy to Cloudflare Workers
pnpm deploy

# Output will show your URL:
# Published octopoid-server
#   https://octopoid-server.your-username.workers.dev
```

### Step 2: Setup Client

```bash
# Install globally
npm install -g octopoid

# Navigate to your project
cd ~/projects/my-app

# Initialize
octopoid init \
  --server https://octopoid-server.your-username.workers.dev \
  --cluster production

# This creates:
# .octopoid/
# ├── config.yaml       # Configuration
# ├── agents.yaml       # Agent definitions
# ├── runtime/          # PIDs, locks
# ├── logs/             # Log files
# └── worktrees/        # Git worktrees
```

### Step 3: Create First Task

```bash
# Create a task
octopoid enqueue "Implement dark mode" --role implement --priority P1

# Output:
# ✅ Task created successfully!
#   ID: task-abc123
#   Queue: incoming
#   Priority: P1
```

### Step 4: Start Orchestrator

```bash
# Start in foreground (for testing)
octopoid start

# Or run as daemon
octopoid start --daemon

# Check status
octopoid status
```

## Verification

Check that everything is working:

```bash
# Server health
curl https://your-server/api/health
# {"status":"healthy","version":"2.0.0"}

# List tasks
octopoid list

# Check orchestrator status
octopoid status
# Should show:
# ✓ Connected to server
# ✓ Running (PID: 12345)
# Tasks:
#   Incoming: 1
#   Claimed: 0
#   Done: 0
```

## Next Steps

- **Configure agents:** Edit `.octopoid/agents.yaml` to customize agent behavior
- **Add more tasks:** Use `octopoid enqueue` to create tasks
- **Monitor progress:** Use `octopoid status` and `octopoid list`
- **Run on VM:** Deploy orchestrator on cloud VM with GPU for heavy workloads

## Common Use Cases

### Queue work locally, execute on VM

```bash
# On laptop
cd ~/project
octopoid enqueue "Implement feature X" --role implement

# On GPU VM
ssh gpu-vm
npm install -g octopoid
octopoid init --server https://... --machine-id gpu-vm-001
octopoid start --daemon
```

### Multiple machines without conflicts

```bash
# Mac Studio
octopoid init --server https://... --machine-id mac-studio
octopoid start --daemon

# Linux workstation
octopoid init --server https://... --machine-id linux-ws
octopoid start --daemon

# Server coordinates - no double-claiming!
```

### Quick setup in new project

```bash
cd ~/new-project
octopoid init --server https://octopoid.example.com
octopoid start
# Done! Project now has AI orchestration
```

## Help

```bash
# CLI help
octopoid --help

# Command help
octopoid init --help
octopoid enqueue --help

# Documentation
https://docs.octopoid.dev
```

## Cost

**Cloudflare Workers Free Tier:**
- 100,000 requests/day
- 10GB D1 database
- Sufficient for small to medium teams

**Paid plans** available for higher limits.
