#!/usr/bin/env node
/**
 * Example: Auto-approve low-risk tasks
 *
 * This script automatically approves tasks that meet certain criteria:
 * - Documentation tasks with commits
 * - Test tasks with commits
 *
 * Usage:
 *   OCTOPOID_SERVER_URL=https://... npm run example:auto-approve
 */

import { OctopoidSDK } from '../src/index'

async function main() {
  const sdk = new OctopoidSDK()

  console.log('🔍 Checking for tasks to auto-approve...\n')

  // Get provisional tasks (awaiting review)
  const tasks = await sdk.tasks.list({ queue: 'provisional' })

  if (tasks.length === 0) {
    console.log('No tasks awaiting review')
    return
  }

  console.log(`Found ${tasks.length} task(s) awaiting review\n`)

  let approved = 0

  for (const task of tasks) {
    const shouldAutoApprove =
      // Auto-approve docs tasks with commits
      (task.role === 'docs' && (task.commits_count || 0) > 0) ||
      // Auto-approve test tasks with commits
      (task.role === 'test' && (task.commits_count || 0) > 0)

    if (shouldAutoApprove) {
      console.log(`✅ Auto-approving ${task.role} task: ${task.id}`)

      await sdk.tasks.accept(task.id, {
        accepted_by: 'auto-approve-script',
      })

      approved++
    } else {
      console.log(`⏭️  Skipping ${task.role || 'unknown'} task: ${task.id}`)
    }
  }

  console.log(`\n✅ Auto-approved ${approved} task(s)`)
}

main().catch((error) => {
  console.error('❌ Error:', error.message)
  process.exit(1)
})
