/**
 * List command implementation
 * Lists tasks with filters
 */

import chalk from 'chalk'
import type { TaskQueue, TaskRole, TaskPriority } from '@octopoid/shared'
import { listTasks } from '../db-interface'

export interface ListOptions {
  queue?: string
  priority?: string
  role?: string
  limit?: number
}

export async function listCommand(options: ListOptions): Promise<void> {
  try {
    console.log(chalk.bold('📋 Listing tasks...'))
    console.log('')

    const filters: any = {}

    if (options.queue) {
      filters.queue = options.queue.split(',') as TaskQueue[]
    }
    if (options.priority) {
      filters.priority = options.priority.split(',') as TaskPriority[]
    }
    if (options.role) {
      filters.role = options.role.split(',') as TaskRole[]
    }
    if (options.limit) {
      filters.limit = options.limit
    }

    const tasks = await listTasks(filters)

    if (tasks.length === 0) {
      console.log(chalk.gray('No tasks found'))
      return
    }

    console.log(chalk.bold(`Found ${tasks.length} task(s):`))
    console.log('')

    // Group by queue
    const grouped = new Map<string, typeof tasks>()
    for (const task of tasks) {
      const queue = task.queue
      if (!grouped.has(queue)) {
        grouped.set(queue, [])
      }
      grouped.get(queue)!.push(task)
    }

    for (const [queue, queueTasks] of grouped) {
      console.log(chalk.bold(`${queue.toUpperCase()} (${queueTasks.length}):`))
      for (const task of queueTasks) {
        const priorityColor =
          task.priority === 'P0'
            ? chalk.red
            : task.priority === 'P1'
            ? chalk.yellow
            : chalk.gray

        console.log(
          `  ${priorityColor(task.priority)} ${task.id} - ${task.role || 'no role'}`
        )
        if (task.claimed_by) {
          console.log(chalk.gray(`     Claimed by: ${task.claimed_by}`))
        }
        if (task.project_id) {
          console.log(chalk.gray(`     Project: ${task.project_id}`))
        }
      }
      console.log('')
    }
  } catch (error) {
    console.error(chalk.red('❌ Error listing tasks:'))
    console.error(error instanceof Error ? error.message : error)
    process.exit(1)
  }
}
