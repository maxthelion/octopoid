# Contributing to Octopoid

## Dev Environment Setup

### Prerequisites

- **Node.js** (v18+) and **pnpm** — for TypeScript packages
- **Python 3.11+** — for the orchestrator and SDK
- **Claude Code** (`claude` CLI) — agents run as Claude Code subprocesses
- **Git** with worktree support

### 1. Clone the Repository

```bash
git clone --recurse-submodules https://github.com/maxthelion/octopoid.git
cd octopoid
```

If you already cloned without submodules:

```bash
git submodule update --init
```

### 2. Install Node Dependencies

```bash
pnpm install
pnpm build
```

### 3. Install Python Dependencies

```bash
pip install -e ".[dev]"
```

Or install just the orchestrator dependencies:

```bash
pip install -r requirements.txt
```

### 4. Link the CLI Globally

```bash
cd packages/client
npm link
cd ../..

# Verify
octopoid --version
```

### 5. Set Your API Key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# Add to ~/.zshrc or ~/.bashrc for persistence
```

### 6. Start a Local Server (for Development)

The server submodule is at `submodules/server/`:

```bash
cd submodules/server
npm install
npx wrangler dev --port 8787
```

### 7. Initialize Octopoid Against the Local Server

```bash
octopoid init --server http://localhost:8787 --cluster dev
```

## Running Tests

The test suite uses `pytest` for Python and requires a running local server.

```bash
# Start the test server (separate terminal)
tests/integration/bin/start-test-server.sh

# Run all tests
pytest tests/

# Run only unit tests (no server required)
pytest tests/unit/

# Run integration tests
pytest tests/integration/
```

See [docs/testing.md](docs/testing.md) for the full testing guide.

## Project Structure

```
orchestrator/      # Python scheduler, agent pool, flows
packages/
  client/          # CLI (TypeScript/Node)
  python-sdk/      # Python SDK for the server API
  dashboard/       # Textual TUI
submodules/
  server/          # Cloudflare Workers server (separate repo)
docs/              # Architecture docs
.octopoid/         # Runtime config (agents, flows, tasks)
tests/             # Test suite
```

## Making Changes

### Python (orchestrator)

After editing `.py` files in `orchestrator/`, clear the bytecode cache so the scheduler picks up your changes:

```bash
find orchestrator -name '__pycache__' -type d -exec rm -rf {} +
```

### TypeScript

Rebuild after changes:

```bash
pnpm build
```

### Commit Style

Use conventional commits:

```
feat: add X
fix: resolve Y
refactor: simplify Z
test: add tests for W
docs: update setup guide
```

Make atomic commits — one logical change per commit.

## Flows and Architecture

The task lifecycle is controlled by a declarative flow system. Read [docs/flows.md](docs/flows.md) before making changes to task state transitions.

## Submitting Changes

1. Fork the repo and create a feature branch from `main`
2. Make your changes with tests
3. Run the test suite (`pytest tests/`) — all tests must pass
4. Open a pull request against `main`
