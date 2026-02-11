# Server Test Suite

Test suite for the Octopoid server (Cloudflare Workers).

## Running Tests

```bash
# Run all tests
npm test

# Run with coverage
npm run test:coverage

# Run in watch mode
npm run test:watch
```

## Test Structure

```
tests/
├── integration.test.ts      # End-to-end API tests with real HTTP server
├── state-machine.test.ts    # State transition logic tests
├── routes-tasks.test.ts     # Task API endpoint tests
├── routes-orchestrators.test.ts  # Orchestrator API tests
└── scheduled.test.ts        # Scheduled job tests
```

## Integration Tests

The `integration.test.ts` file contains end-to-end tests that start a real wrangler dev server:

- **Health Check**: Verifies server status and database connection
- **Orchestrator Lifecycle**: Registration, heartbeat, re-registration
- **Task CRUD**: Creating, reading, listing, filtering tasks
- **Task Lifecycle**: Full flow from claim → submit → accept/reject
- **Concurrent Operations**: Multiple orchestrators claiming tasks
- **Error Handling**: 404s, 400s, invalid operations

```bash
# Run integration tests specifically
npm run test:integration
```

**Note**: Integration tests require D1 database migrations to be applied first:
```bash
npx wrangler d1 migrations apply octopoid-db --local
```

## Writing Tests

Tests use Vitest framework with the following conventions:

```typescript
import { describe, it, expect, beforeEach } from 'vitest'

describe('Feature Name', () => {
  beforeEach(() => {
    // Setup before each test
  })

  it('should do something', () => {
    // Arrange
    const input = 'test'

    // Act
    const result = someFunction(input)

    // Assert
    expect(result).toBe('expected')
  })
})
```

## Mocking D1 Database

For tests that interact with the database, use the D1 miniflare mock:

```typescript
import { unstable_dev } from 'wrangler'

describe('Database Tests', () => {
  let worker: UnstableDevWorker

  beforeAll(async () => {
    worker = await unstable_dev('src/index.ts', {
      experimental: { disableExperimentalWarning: true },
    })
  })

  afterAll(async () => {
    await worker.stop()
  })

  it('should query database', async () => {
    const resp = await worker.fetch('/api/v1/tasks')
    expect(resp.status).toBe(200)
  })
})
```

## Test Coverage Goals

- State machine: 100% coverage
- API routes: 90%+ coverage
- Scheduled jobs: 80%+ coverage
- Overall: 85%+ coverage

## CI Integration

Tests run automatically on:
- Every push to feature branches
- Pull requests to main
- Scheduled nightly builds

See `.github/workflows/ci.yml` for CI configuration.
