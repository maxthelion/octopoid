/**
 * Enqueue command tests
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { existsSync, readFileSync, rmSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { enqueueCommand } from '../src/commands/enqueue'
import * as dbInterface from '../src/db-interface'
import * as config from '../src/config'

describe('enqueueCommand', () => {
  let testDir: string
  let originalCwd: string

  beforeEach(() => {
    // Create temporary test directory
    testDir = join(tmpdir(), `octopoid-test-${Date.now()}`)
    mkdirSync(testDir, { recursive: true })
    mkdirSync(join(testDir, '.octopoid', 'tasks'), { recursive: true })

    originalCwd = process.cwd()
    process.chdir(testDir)

    // Mock config
    vi.spyOn(config, 'loadConfig').mockReturnValue({
      repo: { path: testDir },
      server: { url: 'http://localhost:3000' },
    } as any)

    // Mock createTask to avoid hitting real API
    vi.spyOn(dbInterface, 'createTask').mockResolvedValue({
      id: 'test-task-id',
      file_path: join(testDir, '.octopoid', 'tasks', 'TASK-test-task-id.md'),
      queue: 'incoming',
      priority: 'P2',
      branch: 'main',
      commits_count: 0,
      attempt_count: 0,
      has_plan: false,
      auto_accept: false,
      rejection_count: 0,
      needs_rebase: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      version: 1,
    } as any)
  })

  afterEach(() => {
    process.chdir(originalCwd)
    if (existsSync(testDir)) {
      rmSync(testDir, { recursive: true, force: true })
    }
    vi.restoreAllMocks()
  })

  it('should create task in database and file system', async () => {
    await enqueueCommand('Test task description', {
      role: 'implement',
      priority: 'P1',
    })

    // Verify createTask was called with correct parameters
    expect(dbInterface.createTask).toHaveBeenCalledWith(
      expect.objectContaining({
        queue: 'incoming',
        priority: 'P1',
        role: 'implement',
      })
    )

    // Verify task file was created
    const taskFiles = existsSync(join(testDir, '.octopoid', 'tasks'))
    expect(taskFiles).toBe(true)
  })

  it('should create task file with YAML frontmatter', async () => {
    // Override the mock to use a predictable task ID
    const mockTaskId = 'abc123'
    vi.spyOn(dbInterface, 'createTask').mockResolvedValue({
      id: mockTaskId,
      file_path: join(testDir, '.octopoid', 'tasks', `TASK-${mockTaskId}.md`),
      queue: 'incoming',
      priority: 'P2',
      branch: 'main',
      commits_count: 0,
      attempt_count: 0,
      has_plan: false,
      auto_accept: false,
      rejection_count: 0,
      needs_rebase: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      version: 1,
    } as any)

    await enqueueCommand('Test task description', {
      role: 'implement',
      priority: 'P1',
    })

    // Find the created task file
    const tasksDir = join(testDir, '.octopoid', 'tasks')
    const files = existsSync(tasksDir) ? require('node:fs').readdirSync(tasksDir) : []
    expect(files.length).toBeGreaterThan(0)

    // Read the task file
    const taskFile = join(tasksDir, files[0])
    const content = readFileSync(taskFile, 'utf-8')

    // Verify YAML frontmatter exists
    expect(content).toMatch(/^---\n/)
    expect(content).toContain('title: "Test task description"')
    expect(content).toContain('priority: P1')
    expect(content).toContain('role: implement')
    expect(content).toContain('queue: incoming')
    expect(content).toContain('created_by: human')

    // Verify body sections exist
    expect(content).toContain('## Context')
    expect(content).toContain('## Acceptance Criteria')
  })

  it('should handle missing role', async () => {
    await enqueueCommand('Test task without role', {
      priority: 'P2',
    })

    expect(dbInterface.createTask).toHaveBeenCalledWith(
      expect.objectContaining({
        queue: 'incoming',
        priority: 'P2',
        role: undefined,
      })
    )
  })

  it('should reject invalid role', async () => {
    const exitSpy = vi.spyOn(process, 'exit').mockImplementation(() => {
      throw new Error('process.exit called')
    })

    await expect(
      enqueueCommand('Test task', {
        role: 'invalid-role',
      })
    ).rejects.toThrow('process.exit called')

    expect(exitSpy).toHaveBeenCalledWith(1)
  })

  it('should reject invalid priority', async () => {
    const exitSpy = vi.spyOn(process, 'exit').mockImplementation(() => {
      throw new Error('process.exit called')
    })

    await expect(
      enqueueCommand('Test task', {
        priority: 'P5',
      })
    ).rejects.toThrow('process.exit called')

    expect(exitSpy).toHaveBeenCalledWith(1)
  })
})
