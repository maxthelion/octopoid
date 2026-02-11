# Client Test Suite

Test suite for the Octopoid client library and CLI.

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
├── integration.test.ts      # End-to-end client-server communication tests
├── config.test.ts           # Configuration loading and detection
├── offline-mode.test.ts     # Offline mode and sync manager
├── queue-utils.test.ts      # Task file parsing and management
├── git-utils.test.ts        # Git operations and worktrees
├── scheduler.test.ts        # Scheduler and agent spawning
└── api-client.test.ts       # API client HTTP requests
```

## Integration Tests

The `integration.test.ts` file tests real client-server communication:

- **API Client**: Health checks, registration, heartbeats
- **Task Operations**: CRUD operations through API
- **Task Lifecycle**: Complete flow including claim/submit/accept/reject
- **Offline Mode**: Network error handling
- **Concurrent Operations**: Multiple orchestrators working simultaneously
- **Error Handling**: Invalid requests, missing tasks, etc.

```bash
# Run integration tests
npm run test:integration
```

**Requirements**: Integration tests need a running server at `http://localhost:8787` (or set `TEST_SERVER_URL` env var):

```bash
# Terminal 1: Start server
cd packages/server && npx wrangler dev

# Terminal 2: Run integration tests
cd packages/client && npm run test:integration
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

## Mocking

### Mock File System

```typescript
import { vol } from 'memfs'
import { vi } from 'vitest'

// Mock fs module
vi.mock('node:fs', () => ({
  ...vi.importActual('node:fs'),
  readFileSync: vi.fn(),
}))
```

### Mock API Calls

```typescript
import { vi } from 'vitest'

// Mock fetch
global.fetch = vi.fn(() =>
  Promise.resolve({
    ok: true,
    json: async () => ({ tasks: [] }),
  })
)
```

### Mock Git Operations

```typescript
import { vi } from 'vitest'

vi.mock('simple-git', () => ({
  simpleGit: () => ({
    clone: vi.fn(),
    checkout: vi.fn(),
    pull: vi.fn(),
  }),
}))
```

## Testing Offline Mode

To test offline behavior, mock network failures:

```typescript
it('should fall back to local cache on network error', async () => {
  // Mock fetch to throw network error
  global.fetch = vi.fn(() =>
    Promise.reject(new Error('fetch failed'))
  )

  // Call should fall back to local cache
  const tasks = await listTasks({ queue: 'incoming' })

  expect(tasks).toBeDefined()
  // Verify it used local cache
})
```

## Test Coverage Goals

- Configuration: 90%+ coverage
- Offline mode: 85%+ coverage
- Queue utilities: 90%+ coverage
- Git utilities: 80%+ coverage
- Scheduler: 75%+ coverage
- Overall: 80%+ coverage

## Integration Tests

For end-to-end testing:

```bash
# Start test server
npm run test:server

# Run integration tests
npm run test:integration
```

## CI Integration

Tests run automatically on:
- Every push to feature branches
- Pull requests to main
- Scheduled nightly builds

See `.github/workflows/ci.yml` for CI configuration.
