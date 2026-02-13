/**
 * Queue utilities for task file management
 * Handles reading/writing/moving task markdown files
 */

import { readFileSync, writeFileSync, existsSync, readdirSync, renameSync, mkdirSync, appendFileSync } from 'node:fs'
import { join, basename } from 'node:path'
import { randomBytes } from 'node:crypto'
import type { Task, TaskQueue, TaskPriority, TaskRole } from '@octopoid/shared'
import { loadConfig } from './config'

// All queue directories
export const ALL_QUEUE_DIRS: TaskQueue[] = [
  'incoming',
  'claimed',
  'provisional',
  'done',
  'blocked',
  'backlog',
]

/**
 * Task file structure
 */
export interface TaskFile {
  id: string
  filePath: string
  metadata: TaskMetadata
  body: string
  content: string
}

/**
 * Task metadata from file
 */
export interface TaskMetadata {
  id: string
  title: string
  role?: TaskRole
  priority: TaskPriority
  branch: string
  created?: string
  created_by?: string
  blocked_by?: string
  breakdown_depth?: number
  claimed_by?: string
  claimed_at?: string
  submitted_at?: string
  completed_at?: string
  checks?: string[]
  skip_pr?: boolean
  expedite?: boolean
  wip_branch?: string
  last_agent?: string
  continuation_reason?: string
  project_id?: string
  [key: string]: unknown
}

/**
 * Get queue directory path
 */
export function getQueueDir(): string {
  const config = loadConfig()
  const repoPath = config.repo?.path || process.cwd()
  return join(repoPath, 'tasks')
}

/**
 * Get queue subdirectory path
 */
export function getQueueSubdir(queue: TaskQueue): string {
  const queueDir = getQueueDir()
  const subdir = join(queueDir, queue)

  // Create if doesn't exist
  if (!existsSync(subdir)) {
    mkdirSync(subdir, { recursive: true })
  }

  return subdir
}

/**
 * Generate a unique task ID
 */
export function generateTaskId(): string {
  const timestamp = Date.now().toString(36)
  const random = randomBytes(4).toString('hex')
  return `${timestamp}-${random}`
}

/**
 * Find a task file by ID across all queues
 */
export function findTaskFile(taskId: string): string | null {
  const queueDir = getQueueDir()
  const filename = `TASK-${taskId}.md`

  for (const queue of ALL_QUEUE_DIRS) {
    const candidate = join(queueDir, queue, filename)
    if (existsSync(candidate)) {
      return candidate
    }
  }

  return null
}

/**
 * Parse task markdown file
 */
export function parseTaskFile(filePath: string): TaskFile | null {
  if (!existsSync(filePath)) {
    return null
  }

  try {
    const content = readFileSync(filePath, 'utf-8')
    return parseTaskContent(filePath, content)
  } catch (error) {
    console.error(`Error parsing task file ${filePath}:`, error)
    return null
  }
}

/**
 * Parse task content (for testing or in-memory operations)
 */
export function parseTaskContent(filePath: string, content: string): TaskFile {
  // Extract task ID from title line: # [TASK-abc123] Title
  const titleMatch = content.match(/^#\s*\[TASK-([^\]]+)\]\s*(.+)$/m)
  const taskId = titleMatch ? titleMatch[1] : basename(filePath, '.md').replace('TASK-', '')
  const title = titleMatch ? titleMatch[2].trim() : basename(filePath, '.md')

  // Extract metadata fields (key-value pairs at top of file)
  const metadata: TaskMetadata = {
    id: taskId,
    title,
    priority: 'P2',
    branch: 'main',
  }

  // Helper to extract field value
  const extractField = (fieldName: string): string | undefined => {
    const match = content.match(new RegExp(`^${fieldName}:\\s*(.+)$`, 'm'))
    return match ? match[1].trim() : undefined
  }

  // Helper to parse boolean
  const parseBool = (value: string | undefined): boolean => {
    if (!value) return false
    return ['true', 'yes', '1'].includes(value.toLowerCase())
  }

  // Extract all metadata fields
  metadata.role = extractField('ROLE') as TaskRole | undefined
  metadata.priority = (extractField('PRIORITY') as TaskPriority) || 'P2'
  metadata.branch = extractField('BRANCH') || 'main'
  metadata.created = extractField('CREATED')
  metadata.created_by = extractField('CREATED_BY')
  metadata.blocked_by = extractField('BLOCKED_BY')

  // Parse breakdown_depth as number
  const breakdownDepthStr = extractField('BREAKDOWN_DEPTH')
  if (breakdownDepthStr) {
    const depth = parseInt(breakdownDepthStr, 10)
    if (!isNaN(depth)) {
      metadata.breakdown_depth = depth
    }
  }

  metadata.claimed_by = extractField('CLAIMED_BY')
  metadata.claimed_at = extractField('CLAIMED_AT')
  metadata.submitted_at = extractField('SUBMITTED_AT')
  metadata.completed_at = extractField('COMPLETED_AT')
  metadata.wip_branch = extractField('WIP_BRANCH')
  metadata.last_agent = extractField('LAST_AGENT')
  metadata.continuation_reason = extractField('CONTINUATION_REASON')
  metadata.project_id = extractField('PROJECT_ID')

  // Parse checks (comma-separated)
  const checksStr = extractField('CHECKS')
  if (checksStr) {
    metadata.checks = checksStr.split(',').map((c) => c.trim()).filter(Boolean)
  }

  // Parse booleans
  metadata.skip_pr = parseBool(extractField('SKIP_PR'))
  metadata.expedite = parseBool(extractField('EXPEDITE'))

  // Extract body (everything after metadata section)
  // Look for first markdown heading or content after metadata
  const metadataEndMatch = content.match(/\n\n## /)
  const body = metadataEndMatch
    ? content.substring(content.indexOf(metadataEndMatch[0]))
    : content

  return {
    id: taskId,
    filePath,
    metadata,
    body: body.trim(),
    content,
  }
}

/**
 * List task files in a queue
 */
export function listTaskFiles(queue: TaskQueue): TaskFile[] {
  const queueDir = getQueueSubdir(queue)
  const files = readdirSync(queueDir).filter((f) => f.endsWith('.md'))

  const tasks: TaskFile[] = []
  for (const file of files) {
    const filePath = join(queueDir, file)
    const task = parseTaskFile(filePath)
    if (task) {
      tasks.push(task)
    }
  }

  // Sort by: 1) expedite flag, 2) priority, 3) created time
  const priorityOrder: Record<string, number> = { P0: 0, P1: 1, P2: 2, P3: 3 }
  tasks.sort((a, b) => {
    // Expedited tasks first
    if (a.metadata.expedite && !b.metadata.expedite) return -1
    if (!a.metadata.expedite && b.metadata.expedite) return 1

    // Then by priority
    const aPrio = priorityOrder[a.metadata.priority] ?? 2
    const bPrio = priorityOrder[b.metadata.priority] ?? 2
    if (aPrio !== bPrio) return aPrio - bPrio

    // Then by created time
    const aCreated = a.metadata.created || ''
    const bCreated = b.metadata.created || ''
    return aCreated.localeCompare(bCreated)
  })

  return tasks
}

/**
 * Count tasks in a queue
 */
export function countQueue(queue: TaskQueue): number {
  const queueDir = getQueueSubdir(queue)
  const files = readdirSync(queueDir).filter((f) => f.endsWith('.md'))
  return files.length
}

/**
 * Create a new task file
 */
export interface CreateTaskFileOptions {
  id: string
  title: string
  role?: TaskRole
  priority?: TaskPriority
  branch?: string
  description?: string
  created_by?: string
  blocked_by?: string
  breakdown_depth?: number
  project_id?: string
  checks?: string[]
}

export function createTaskFile(
  queue: TaskQueue,
  options: CreateTaskFileOptions
): string {
  const {
    id,
    title,
    role,
    priority = 'P2',
    branch = 'main',
    description = '',
    created_by,
    blocked_by,
    breakdown_depth,
    project_id,
    checks = [],
  } = options

  const queueDir = getQueueSubdir(queue)
  const filename = `TASK-${id}.md`
  const filePath = join(queueDir, filename)

  // Build content
  const lines: string[] = []

  // Title
  lines.push(`# [TASK-${id}] ${title}`)
  lines.push('')

  // Metadata
  if (role) lines.push(`ROLE: ${role}`)
  lines.push(`PRIORITY: ${priority}`)
  lines.push(`BRANCH: ${branch}`)
  lines.push(`CREATED: ${new Date().toISOString()}`)
  if (created_by) lines.push(`CREATED_BY: ${created_by}`)
  if (blocked_by) lines.push(`BLOCKED_BY: ${blocked_by}`)
  if (breakdown_depth !== undefined) lines.push(`BREAKDOWN_DEPTH: ${breakdown_depth}`)
  if (project_id) lines.push(`PROJECT_ID: ${project_id}`)
  if (checks.length > 0) lines.push(`CHECKS: ${checks.join(', ')}`)

  lines.push('')

  // Description
  if (description) {
    lines.push('## Description')
    lines.push('')
    lines.push(description)
    lines.push('')
  }

  // Requirements section
  lines.push('## Requirements')
  lines.push('')
  lines.push('- [ ] TODO: Add requirements')
  lines.push('')

  // Acceptance Criteria
  lines.push('## Acceptance Criteria')
  lines.push('')
  lines.push('- [ ] TODO: Add acceptance criteria')
  lines.push('')

  const content = lines.join('\n')
  writeFileSync(filePath, content, 'utf-8')

  return filePath
}

/**
 * Move task file between queues
 */
export function moveTaskFile(
  taskId: string,
  fromQueue: TaskQueue,
  toQueue: TaskQueue,
  appendMetadata?: Record<string, string>
): string | null {
  const fromDir = getQueueSubdir(fromQueue)
  const toDir = getQueueSubdir(toQueue)
  const filename = `TASK-${taskId}.md`

  const sourcePath = join(fromDir, filename)
  const destPath = join(toDir, filename)

  if (!existsSync(sourcePath)) {
    console.error(`Task file not found: ${sourcePath}`)
    return null
  }

  try {
    // Append metadata if provided
    if (appendMetadata) {
      const lines: string[] = []
      for (const [key, value] of Object.entries(appendMetadata)) {
        lines.push(`${key}: ${value}`)
      }
      if (lines.length > 0) {
        appendFileSync(sourcePath, '\n' + lines.join('\n') + '\n', 'utf-8')
      }
    }

    // Atomic rename
    renameSync(sourcePath, destPath)
    return destPath
  } catch (error) {
    console.error(`Error moving task file ${sourcePath} to ${destPath}:`, error)
    return null
  }
}

/**
 * Update task file with new metadata
 */
export function updateTaskFileMetadata(
  filePath: string,
  updates: Record<string, string>
): boolean {
  if (!existsSync(filePath)) {
    return false
  }

  try {
    let content = readFileSync(filePath, 'utf-8')

    // Update or add each field
    for (const [key, value] of Object.entries(updates)) {
      const regex = new RegExp(`^${key}:\\s*.+$`, 'm')
      if (regex.test(content)) {
        // Update existing field
        content = content.replace(regex, `${key}: ${value}`)
      } else {
        // Add new field after title
        const titleMatch = content.match(/^#\s*\[TASK-[^\]]+\].*$/m)
        if (titleMatch) {
          const insertPos = titleMatch.index! + titleMatch[0].length
          content =
            content.slice(0, insertPos) +
            `\n${key}: ${value}` +
            content.slice(insertPos)
        } else {
          // No title found, append at top
          content = `${key}: ${value}\n${content}`
        }
      }
    }

    writeFileSync(filePath, content, 'utf-8')
    return true
  } catch (error) {
    console.error(`Error updating task file ${filePath}:`, error)
    return false
  }
}

/**
 * Append content to task file
 */
export function appendToTaskFile(
  filePath: string,
  content: string
): boolean {
  if (!existsSync(filePath)) {
    return false
  }

  try {
    appendFileSync(filePath, '\n' + content + '\n', 'utf-8')
    return true
  } catch (error) {
    console.error(`Error appending to task file ${filePath}:`, error)
    return false
  }
}

/**
 * Delete task file
 */
export function deleteTaskFile(filePath: string): boolean {
  if (!existsSync(filePath)) {
    return false
  }

  try {
    const { unlinkSync } = require('node:fs')
    unlinkSync(filePath)
    return true
  } catch (error) {
    console.error(`Error deleting task file ${filePath}:`, error)
    return false
  }
}

/**
 * Get task file path for a task ID and queue
 */
export function getTaskFilePath(taskId: string, queue: TaskQueue): string {
  const queueDir = getQueueSubdir(queue)
  return join(queueDir, `TASK-${taskId}.md`)
}

/**
 * Check if task file exists
 */
export function taskFileExists(taskId: string, queue?: TaskQueue): boolean {
  if (queue) {
    const filePath = getTaskFilePath(taskId, queue)
    return existsSync(filePath)
  }

  // Check all queues
  return findTaskFile(taskId) !== null
}

/**
 * Sync task file with database task
 * Updates file metadata to match database state
 */
export function syncTaskFileWithDB(task: Task): boolean {
  const filePath = findTaskFile(task.id)
  if (!filePath) {
    console.warn(`Task file not found for ${task.id}, creating new one`)
    // Create file in appropriate queue
    createTaskFile(task.queue as TaskQueue, {
      id: task.id,
      title: `Task ${task.id}`,
      role: task.role || undefined,
      priority: task.priority,
      branch: task.branch,
      blocked_by: task.blocked_by || undefined,
      project_id: task.project_id || undefined,
    })
    return true
  }

  // Update metadata to match DB
  const updates: Record<string, string> = {}

  if (task.claimed_by) updates.CLAIMED_BY = task.claimed_by
  if (task.claimed_at) updates.CLAIMED_AT = task.claimed_at
  if (task.submitted_at) updates.SUBMITTED_AT = task.submitted_at
  if (task.completed_at) updates.COMPLETED_AT = task.completed_at
  if (task.blocked_by) updates.BLOCKED_BY = task.blocked_by

  return updateTaskFileMetadata(filePath, updates)
}
