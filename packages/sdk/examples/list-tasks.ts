#!/usr/bin/env node
/**
 * Example: List tasks by queue
 *
 * Usage:
 *   OCTOPOID_SERVER_URL=https://... npm run example:list-tasks
 */

import { OctopoidSDK } from '../src/index'

async function main() {
  const sdk = new OctopoidSDK()

  console.log('📊 Task Summary\n')

  // Get tasks by queue
  const queues = ['incoming', 'claimed', 'provisional', 'done'] as const

  for (const queue of queues) {
    const tasks = await sdk.tasks.list({ queue, limit: 100 })

    console.log(`${queue.toUpperCase()}: ${tasks.length}`)

    if (tasks.length > 0) {
      // Show first 3 tasks
      tasks.slice(0, 3).forEach((task) => {
        console.log(`  • ${task.id} (${task.priority}) - ${task.role || 'no role'}`)
      })

      if (tasks.length > 3) {
        console.log(`  ... and ${tasks.length - 3} more`)
      }
    }

    console.log('')
  }
}

main().catch((error) => {
  console.error('❌ Error:', error.message)
  process.exit(1)
})
