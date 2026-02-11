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
├── config.test.ts           # Configuration loading and detection
├── offline-mode.test.ts     # Offline mode and sync manager
├── queue-utils.test.ts      # Task file parsing and management
├── git-utils.test.ts        # Git operations and worktrees
├── scheduler.test.ts        # Scheduler and agent spawning
└── api-client.test.ts       # API client HTTP requests
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
