# Octopoid SDK for Node.js/TypeScript

Write custom scripts and automation for your Octopoid orchestrator.

## Installation

```bash
npm install octopoid-sdk
```

## Quick Start

```typescript
import { OctopoidSDK } from 'octopoid-sdk'

// Initialize SDK
const sdk = new OctopoidSDK({
  serverUrl: 'https://octopoid-server.username.workers.dev',
  apiKey: process.env.OCTOPOID_API_KEY
})

// Create a task
const task = await sdk.tasks.create({
  id: 'implement-feature-x',
  file_path: 'tasks/incoming/TASK-implement-feature-x.md',
  queue: 'incoming',
  priority: 'P1',
  role: 'implement'
})

console.log(`Created task: ${task.id}`)
```

## Configuration

The SDK can be configured via constructor options or environment variables:

```typescript
const sdk = new OctopoidSDK({
  serverUrl: 'https://octopoid-server.username.workers.dev',  // or OCTOPOID_SERVER_URL
  apiKey: 'your-api-key',                                     // or OCTOPOID_API_KEY
  timeout: 30000                                              // Request timeout in ms
})
```

## API Reference

### Tasks

#### Create a Task

```typescript
const task = await sdk.tasks.create({
  id: 'my-task-123',
  file_path: 'tasks/incoming/TASK-my-task-123.md',
  queue: 'incoming',
  priority: 'P0',  // P0 (highest) to P3 (lowest)
  role: 'implement',
  branch: 'main',
  project_id: 'my-project'
})
```

#### Get a Task

```typescript
const task = await sdk.tasks.get('my-task-123')

if (task) {
  console.log(`Task ${task.id} is in ${task.queue} queue`)
}
```

#### List Tasks

```typescript
// List all incoming tasks
const tasks = await sdk.tasks.list({ queue: 'incoming' })

// Filter by priority
const p0Tasks = await sdk.tasks.list({ priority: 'P0' })

// Filter by role
const implementTasks = await sdk.tasks.list({ role: 'implement' })

// Limit results
const recentTasks = await sdk.tasks.list({ limit: 10 })
```

#### Update a Task

```typescript
await sdk.tasks.update('my-task-123', {
  priority: 'P0',
  branch: 'feature/new-feature'
})
```

#### Accept a Task (Manual Approval)

```typescript
await sdk.tasks.accept('my-task-123', {
  accepted_by: 'manual-review'
})
```

#### Reject a Task

```typescript
await sdk.tasks.reject('my-task-123', {
  reason: 'Does not meet coding standards',
  rejected_by: 'code-review'
})
```

### Projects

#### Create a Project

```typescript
const project = await sdk.projects.create({
  id: 'web-app',
  name: 'Web Application',
  description: 'Main web application'
})
```

#### Get a Project

```typescript
const project = await sdk.projects.get('web-app')
```

#### List Projects

```typescript
const projects = await sdk.projects.list()
```

### System

#### Health Check

```typescript
const health = await sdk.system.healthCheck()
console.log(`Server status: ${health.status}`)
```

## Example Scripts

### Auto-Approve Low-Risk Tasks

```typescript
import { OctopoidSDK } from 'octopoid-sdk'

const sdk = new OctopoidSDK()

// Find provisional tasks
const tasks = await sdk.tasks.list({ queue: 'provisional' })

for (const task of tasks) {
  // Auto-approve documentation tasks
  if (task.role === 'docs' && task.commits_count > 0) {
    console.log(`Auto-approving docs task: ${task.id}`)
    await sdk.tasks.accept(task.id, {
      accepted_by: 'auto-approve-script'
    })
  }
}
```

### Create Tasks from External Source

```typescript
import { OctopoidSDK } from 'octopoid-sdk'
import { readFileSync } from 'fs'

const sdk = new OctopoidSDK()

// Read feature requests from JSON file
const features = JSON.parse(readFileSync('features.json', 'utf-8'))

for (const feature of features) {
  const taskId = `feature-${feature.id}`

  await sdk.tasks.create({
    id: taskId,
    file_path: `tasks/incoming/TASK-${taskId}.md`,
    queue: 'incoming',
    priority: feature.priority,
    role: 'implement',
    project_id: feature.project
  })

  console.log(`Created task for: ${feature.title}`)
}
```

### Monitor Task Progress

```typescript
import { OctopoidSDK } from 'octopoid-sdk'

const sdk = new OctopoidSDK()

// Poll for task status
const taskId = 'my-task-123'

while (true) {
  const task = await sdk.tasks.get(taskId)

  if (!task) {
    console.log('Task not found')
    break
  }

  console.log(`Task ${task.id}: ${task.queue}`)

  if (task.queue === 'done') {
    console.log('✅ Task completed!')
    break
  }

  if (task.queue === 'provisional') {
    console.log('🔍 Task awaiting review')
    break
  }

  // Wait 10 seconds before next check
  await new Promise(resolve => setTimeout(resolve, 10000))
}
```

### Generate Daily Report

```typescript
import { OctopoidSDK } from 'octopoid-sdk'

const sdk = new OctopoidSDK()

// Get task counts by queue
const [incoming, claimed, provisional, done] = await Promise.all([
  sdk.tasks.list({ queue: 'incoming' }),
  sdk.tasks.list({ queue: 'claimed' }),
  sdk.tasks.list({ queue: 'provisional' }),
  sdk.tasks.list({ queue: 'done' })
])

console.log('📊 Daily Report')
console.log(`Incoming: ${incoming.length}`)
console.log(`In Progress: ${claimed.length}`)
console.log(`Awaiting Review: ${provisional.length}`)
console.log(`Completed: ${done.length}`)

// List today's completed tasks
const today = new Date().toISOString().split('T')[0]
const todaysDone = done.filter(t =>
  t.completed_at && t.completed_at.startsWith(today)
)

console.log(`\n✅ Completed Today: ${todaysDone.length}`)
todaysDone.forEach(t => {
  console.log(`  • ${t.id} (${t.role})`)
})
```

## TypeScript Support

The SDK is written in TypeScript and provides full type definitions:

```typescript
import { OctopoidSDK, type Task, type CreateTaskRequest } from 'octopoid-sdk'

const sdk = new OctopoidSDK()

// All methods are fully typed
const task: Task = await sdk.tasks.create({
  id: 'my-task',
  file_path: 'tasks/incoming/TASK-my-task.md',
  queue: 'incoming',
  priority: 'P1',
  role: 'implement'
})

// TypeScript will catch errors at compile time
// task.priority = 'invalid'  // ❌ TypeScript error
```

## Error Handling

```typescript
import { OctopoidSDK } from 'octopoid-sdk'

const sdk = new OctopoidSDK()

try {
  const task = await sdk.tasks.get('non-existent-task')
  if (task) {
    console.log(`Found task: ${task.id}`)
  } else {
    console.log('Task not found')
  }
} catch (error) {
  console.error('API error:', error)
}
```

## License

MIT

## Contributing

Contributions are welcome! Please see the main [Octopoid repository](https://github.com/org/octopoid) for contribution guidelines.
