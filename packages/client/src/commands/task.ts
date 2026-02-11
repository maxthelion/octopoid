/**
 * Task management commands
 * Operations for approving, rejecting, and managing tasks
 */

import { acceptCompletion, rejectCompletion, getTask } from '../db-interface'

export async function approveCommand(taskId: string, options: { by?: string }): Promise<void> {
  try {
    console.log(`🔍 Approving task ${taskId}...`)

    // Get task to verify it exists
    const task = await getTask(taskId)
    if (!task) {
      console.error(`❌ Task ${taskId} not found`)
      process.exit(1)
    }

    // Check if task is in provisional queue
    if (task.queue !== 'provisional') {
      console.error(`❌ Task ${taskId} is in '${task.queue}' queue, not 'provisional'`)
      console.error('   Only tasks in provisional queue can be approved')
      process.exit(1)
    }

    // Get who is approving (from option or default)
    const acceptedBy = options.by || 'manual-review'

    // Accept the task
    await acceptCompletion(taskId, { accepted_by: acceptedBy })

    console.log(`✅ Task ${taskId} approved by ${acceptedBy}`)
    console.log(`   Task moved to 'done' queue`)
  } catch (error) {
    console.error('❌ Failed to approve task:', (error as Error).message)
    process.exit(1)
  }
}

export async function rejectCommand(
  taskId: string,
  reason: string,
  options: { by?: string }
): Promise<void> {
  try {
    console.log(`🔍 Rejecting task ${taskId}...`)

    if (!reason || reason.trim().length === 0) {
      console.error('❌ Rejection reason is required')
      console.error('   Usage: octopoid reject <task-id> <reason>')
      process.exit(1)
    }

    // Get task to verify it exists
    const task = await getTask(taskId)
    if (!task) {
      console.error(`❌ Task ${taskId} not found`)
      process.exit(1)
    }

    // Check if task is in provisional queue
    if (task.queue !== 'provisional') {
      console.error(`❌ Task ${taskId} is in '${task.queue}' queue, not 'provisional'`)
      console.error('   Only tasks in provisional queue can be rejected')
      process.exit(1)
    }

    // Get who is rejecting (from option or default)
    const rejectedBy = options.by || 'manual-review'

    // Reject the task
    await rejectCompletion(taskId, {
      reason,
      rejected_by: rejectedBy,
    })

    console.log(`❌ Task ${taskId} rejected by ${rejectedBy}`)
    console.log(`   Reason: ${reason}`)
    console.log(`   Task moved back to 'incoming' queue for retry`)
  } catch (error) {
    console.error('❌ Failed to reject task:', (error as Error).message)
    process.exit(1)
  }
}

export async function showTaskCommand(taskId: string): Promise<void> {
  try {
    const task = await getTask(taskId)
    if (!task) {
      console.error(`❌ Task ${taskId} not found`)
      process.exit(1)
    }

    console.log('\n📋 Task Details')
    console.log('─'.repeat(60))
    console.log(`ID:           ${task.id}`)
    console.log(`Queue:        ${task.queue}`)
    console.log(`Role:         ${task.role || 'N/A'}`)
    console.log(`Priority:     ${task.priority || 'N/A'}`)
    console.log(`Branch:       ${task.branch || 'N/A'}`)
    console.log(`Claimed by:   ${task.claimed_by || 'unclaimed'}`)
    if (task.project_id) {
      console.log(`Project:      ${task.project_id}`)
    }
    if (task.blocked_by) {
      console.log(`Blocked by:   ${task.blocked_by}`)
    }
    console.log(`\nProgress:`)
    console.log(`  Commits:    ${task.commits_count || 0}`)
    console.log(`  Turns used: ${task.turns_used || 0}`)
    console.log(`  Attempts:   ${task.attempt_count || 0}`)
    if (task.rejection_count && task.rejection_count > 0) {
      console.log(`  Rejections: ${task.rejection_count}`)
    }
    if (task.pr_number) {
      console.log(`\nPull Request: #${task.pr_number}`)
      if (task.pr_url) {
        console.log(`  URL: ${task.pr_url}`)
      }
    }
    if (task.staging_url) {
      console.log(`\nStaging: ${task.staging_url}`)
    }
    console.log(`\nCreated:  ${task.created_at || 'N/A'}`)
    console.log(`Updated:  ${task.updated_at || 'N/A'}`)
    console.log('─'.repeat(60))
  } catch (error) {
    console.error('❌ Failed to get task:', (error as Error).message)
    process.exit(1)
  }
}
