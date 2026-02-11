#!/usr/bin/env node
/**
 * Example: Create a task
 *
 * Usage:
 *   OCTOPOID_SERVER_URL=https://... npm run example:create-task
 */

import { OctopoidSDK } from '../src/index'

async function main() {
  const sdk = new OctopoidSDK()

  console.log('Creating a new task...')

  const task = await sdk.tasks.create({
    id: `example-${Date.now()}`,
    file_path: `tasks/incoming/TASK-example-${Date.now()}.md`,
    queue: 'incoming',
    priority: 'P2',
    role: 'implement',
    branch: 'main',
  })

  console.log(`✅ Created task: ${task.id}`)
  console.log(`   Queue: ${task.queue}`)
  console.log(`   Priority: ${task.priority}`)
  console.log(`   Role: ${task.role}`)
}

main().catch((error) => {
  console.error('❌ Error:', error.message)
  process.exit(1)
})
