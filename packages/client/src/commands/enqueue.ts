/**
 * Enqueue command implementation
 * Creates a new task
 */

import { randomBytes } from 'node:crypto'
import { join } from 'node:path'
import { writeFileSync, mkdirSync } from 'node:fs'
import chalk from 'chalk'
import type { TaskRole, TaskPriority } from '@octopoid/shared'
import { createTask } from '../db-interface'
import { loadConfig } from '../config'

export interface EnqueueOptions {
  role?: string
  priority?: string
  project?: string
  complexity?: string
}

export async function enqueueCommand(
  description: string,
  options: EnqueueOptions
): Promise<void> {
  try {
    const config = loadConfig()

    // Generate task ID
    const taskId = generateTaskId()

    // Determine file path
    const repoPath = config.repo?.path || process.cwd()
    const filePath = join(repoPath, '.octopoid', 'tasks', `TASK-${taskId}.md`)

    // Validate role
    const validRoles = ['implement', 'breakdown', 'test', 'review', 'fix', 'research']
    const role = options.role as TaskRole | undefined
    if (role && !validRoles.includes(role)) {
      console.error(chalk.red(`❌ Invalid role: ${role}`))
      console.log(`Valid roles: ${validRoles.join(', ')}`)
      process.exit(1)
    }

    // Validate priority
    const validPriorities = ['P0', 'P1', 'P2', 'P3']
    const priority = (options.priority || 'P2') as TaskPriority
    if (!validPriorities.includes(priority)) {
      console.error(chalk.red(`❌ Invalid priority: ${priority}`))
      console.log(`Valid priorities: ${validPriorities.join(', ')}`)
      process.exit(1)
    }

    console.log(chalk.bold('📝 Creating task...'))
    console.log(`  ID: ${taskId}`)
    console.log(`  Description: ${description}`)
    console.log(`  Role: ${role || 'not specified'}`)
    console.log(`  Priority: ${priority}`)

    // Create task in database/API
    const task = await createTask({
      id: taskId,
      file_path: filePath,
      queue: 'incoming',
      priority,
      role,
      project_id: options.project,
      complexity: options.complexity as any,
    })

    // Create task markdown file
    const tasksDir = join(repoPath, '.octopoid', 'tasks')
    mkdirSync(tasksDir, { recursive: true })

    const taskContent = createTaskMarkdown({
      id: taskId,
      title: description,
      role,
      priority,
      project_id: options.project,
      complexity: options.complexity,
    })

    writeFileSync(filePath, taskContent, 'utf-8')

    console.log('')
    console.log(chalk.green('✅ Task created successfully!'))
    console.log(`  ID: ${task.id}`)
    console.log(`  File: ${filePath}`)
    console.log(`  Queue: ${task.queue}`)
    console.log(`  Priority: ${task.priority}`)
    console.log('')
    console.log('The orchestrator will claim and work on it automatically.')
  } catch (error) {
    console.error(chalk.red('❌ Error creating task:'))
    console.error(error instanceof Error ? error.message : error)
    process.exit(1)
  }
}

function generateTaskId(): string {
  const timestamp = Date.now().toString(36)
  const random = randomBytes(4).toString('hex')
  return `task-${timestamp}-${random}`
}

interface TaskMarkdownOptions {
  id: string
  title: string
  role?: string
  priority: string
  project_id?: string
  complexity?: string
}

function createTaskMarkdown(options: TaskMarkdownOptions): string {
  const { id, title, role, priority, project_id, complexity } = options
  const now = new Date().toISOString()

  const frontmatter: string[] = [
    '---',
    `id: TASK-${id}`,
    `title: "${title}"`,
    `priority: ${priority}`,
  ]

  if (role) {
    frontmatter.push(`role: ${role}`)
  }

  frontmatter.push('queue: incoming')
  frontmatter.push('created_by: human')
  frontmatter.push(`created_at: ${now}`)

  if (project_id) {
    frontmatter.push(`project_id: ${project_id}`)
  }

  if (complexity) {
    frontmatter.push(`complexity: ${complexity}`)
  }

  frontmatter.push('---')

  const body = [
    '',
    `# ${title}`,
    '',
    '## Context',
    '',
    'TODO: Add context and background for this task.',
    '',
    '## Acceptance Criteria',
    '',
    '- [ ] TODO: Add acceptance criteria',
    '',
  ]

  return frontmatter.join('\n') + '\n' + body.join('\n')
}
