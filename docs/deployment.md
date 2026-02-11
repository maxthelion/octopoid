# Deployment Guide

## Server Deployment (Cloudflare Workers)

### Prerequisites

- Cloudflare account
- Wrangler CLI installed (`pnpm install`)
- API token with Workers and D1 permissions

### Initial Setup

1. **Create D1 Database:**
```bash
cd packages/server
pnpm db:create
```

This outputs a database_id. Save it for the next step.

2. **Configure wrangler.toml:**
```toml
[[d1_databases]]
binding = "DB"
database_name = "octopoid-db"
database_id = "YOUR_DATABASE_ID_HERE"  # ← Paste from step 1
```

3. **Apply Migrations:**
```bash
pnpm db:migrations:apply
```

4. **Deploy:**
```bash
pnpm deploy
```

Output shows your worker URL:
```
Published octopoid-server (2.3 sec)
  https://octopoid-server.your-username.workers.dev
```

### Environment Variables

Set secrets using Wrangler:

```bash
# API secret key (for admin endpoints)
wrangler secret put API_SECRET_KEY
# Prompt: Enter secret value
# > your-secret-key-here

# Anthropic API key (optional, for future features)
wrangler secret put ANTHROPIC_API_KEY
# Prompt: Enter secret value
# > sk-ant-...
```

### Custom Domain

1. Add domain to Cloudflare
2. Update `wrangler.toml`:
```toml
routes = [
  { pattern = "octopoid.example.com/*", custom_domain = true }
]
```
3. Deploy again: `pnpm deploy`

### Monitoring

**View logs:**
```bash
wrangler tail
```

**View analytics:**
```bash
wrangler dashboard
```

Or visit: https://dash.cloudflare.com/

## Client Deployment

### npm Package (Recommended)

Published to npm as `octopoid`:

```bash
npm install -g octopoid
```

### From Source

```bash
# Clone repository
git clone https://github.com/org/octopoid.git
cd octopoid

# Install dependencies
pnpm install

# Build client
pnpm --filter octopoid build

# Link globally
cd packages/client
npm link

# Verify
octopoid --version
```

## CI/CD Pipeline

### GitHub Actions

Workflows are in `.github/workflows/`:

- **ci.yml:** Runs on every push/PR
  - Type checking
  - Linting
  - Tests
  - Build verification

- **deploy-server.yml:** Deploys server on main branch push
  - Builds packages
  - Deploys to Cloudflare Workers
  - Applies database migrations

- **publish-client.yml:** Publishes to npm on version tags
  - Builds all packages
  - Publishes @octopoid/shared
  - Publishes octopoid client
  - Creates GitHub release

### Required Secrets

Configure in GitHub repository settings:

```
CLOUDFLARE_API_TOKEN      # Cloudflare Workers deployment
CLOUDFLARE_ACCOUNT_ID     # Your Cloudflare account ID
NPM_TOKEN                 # npm publishing token
```

### Release Process

1. Update version in package.json files:
```bash
# Manually edit:
# - packages/shared/package.json
# - packages/client/package.json
# - packages/server/package.json
```

2. Commit and tag:
```bash
git add .
git commit -m "chore: bump version to 2.0.1"
git tag v2.0.1
git push origin main --tags
```

3. GitHub Actions automatically:
- Runs tests
- Builds packages
- Publishes to npm
- Creates GitHub release

## VM Deployment (Orchestrator)

### Ubuntu/Debian VM

```bash
# Install Node.js
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install pnpm
curl -fsSL https://get.pnpm.io/install.sh | sh -

# Install octopoid
npm install -g octopoid

# Configure
octopoid init \
  --server https://octopoid-server.your-username.workers.dev \
  --cluster production \
  --machine-id vm-gpu-001

# Set API key (if required)
export OCTOPOID_API_KEY=your-api-key

# Start as systemd service
sudo tee /etc/systemd/system/octopoid.service > /dev/null <<EOF
[Unit]
Description=Octopoid Orchestrator
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/project
Environment="OCTOPOID_API_KEY=your-api-key"
ExecStart=/usr/bin/octopoid start
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable octopoid
sudo systemctl start octopoid

# Check status
sudo systemctl status octopoid
```

### Docker (Alternative)

```dockerfile
# Dockerfile
FROM node:18-alpine

RUN npm install -g octopoid

WORKDIR /workspace

CMD ["octopoid", "start"]
```

```bash
# Build
docker build -t octopoid-client .

# Run
docker run -d \
  --name octopoid \
  -v $(pwd):/workspace \
  -e OCTOPOID_API_KEY=your-api-key \
  octopoid-client
```

## Multi-Environment Setup

### Staging Environment

1. Deploy separate server:
```bash
# In packages/server/wrangler.toml
name = "octopoid-server-staging"

# Deploy
pnpm deploy
# URL: https://octopoid-server-staging.your-username.workers.dev
```

2. Configure clients:
```bash
octopoid init \
  --server https://octopoid-server-staging.your-username.workers.dev \
  --cluster staging
```

### Production Environment

```bash
octopoid init \
  --server https://octopoid.example.com \
  --cluster production
```

## Health Checks

### Server

```bash
# HTTP health check
curl https://octopoid-server.your-username.workers.dev/api/health

# Expected response:
# {"status":"healthy","version":"2.0.0","timestamp":"...","database":"connected"}
```

### Client

```bash
octopoid status

# Expected output:
# ✓ Connected to server
# ✓ Running (PID: 12345)
# Tasks: Incoming: 5, Claimed: 2, Done: 10
```

## Troubleshooting

### Server won't deploy

```bash
# Check Wrangler authentication
wrangler whoami

# Re-authenticate if needed
wrangler login

# Verify database_id in wrangler.toml
wrangler d1 list
```

### Client can't connect

```bash
# Test server directly
curl https://your-server/api/health

# Check config
cat .octopoid/config.yaml

# Validate config
octopoid validate
```

### Database migration fails

```bash
# Check current schema version
wrangler d1 execute octopoid-db --command "SELECT * FROM schema_info"

# Re-apply migrations
pnpm db:migrations:apply --force
```

## Backup and Recovery

### Database Backup (D1)

```bash
# Export database
wrangler d1 backup create octopoid-db

# List backups
wrangler d1 backup list octopoid-db

# Restore from backup
wrangler d1 backup restore octopoid-db --backup-id=<backup-id>
```

### Manual Backup

```bash
# Export via API (future feature)
curl https://your-server/api/admin/export \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  > backup-$(date +%Y%m%d).json
```

## Scaling

### Horizontal (More VMs)

```bash
# VM 1
octopoid init --machine-id vm-001
octopoid start --daemon

# VM 2
octopoid init --machine-id vm-002
octopoid start --daemon

# Server coordinates automatically
```

### Vertical (Larger VMs)

- Increase VM size (CPU/RAM)
- Increase max_concurrent agents in `.octopoid/agents.yaml`
- Restart orchestrator

### Geographic Distribution

Deploy VMs in multiple regions:
```bash
# US East VM
octopoid init --machine-id us-east-001 --cluster production

# EU VM
octopoid init --machine-id eu-west-001 --cluster production

# Asia VM
octopoid init --machine-id asia-001 --cluster production
```

Server (Cloudflare) automatically routes to nearest edge location.

## Cost Estimation

### Cloudflare Workers (Server)

**Free Tier:**
- 100,000 requests/day
- 10GB D1 storage
- 5M D1 reads/day
- 100K D1 writes/day

**Estimated usage (50 active tasks/day):**
- ~5,000 API requests/day
- ~100MB database storage
- ~10K reads/day
- ~500 writes/day

**Cost:** $0/month (within free tier)

**Paid Plan ($5/month):**
- 10M requests/month
- 50GB D1 storage
- Unlimited D1 operations

### VMs (Orchestrators)

**AWS EC2 example:**
- t3.medium (2 vCPU, 4GB RAM): ~$30/month
- g4dn.xlarge (GPU): ~$390/month

**Google Cloud example:**
- n1-standard-2: ~$50/month
- n1-standard-4 with T4 GPU: ~$300/month

**Recommendation:** Start with free tier server + 1 VM for orchestrator.
