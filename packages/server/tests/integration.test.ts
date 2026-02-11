/**
 * Server Integration Tests
 * Tests actual HTTP endpoints and database operations
 */

import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import { unstable_dev } from 'wrangler'
import type { UnstableDevWorker } from 'wrangler'

describe('Server Integration Tests', () => {
  let worker: UnstableDevWorker
  let baseUrl: string

  beforeAll(async () => {
    // Start wrangler dev server for testing
    worker = await unstable_dev('src/index.ts', {
      experimental: { disableExperimentalWarning: true },
    })
    baseUrl = `http://localhost:${worker.port}`
  }, 30000)

  afterAll(async () => {
    await worker.stop()
  })

  describe('Health Check', () => {
    it('should return healthy status', async () => {
      const response = await fetch(`${baseUrl}/api/health`)
      const data = await response.json()

      expect(response.status).toBe(200)
      expect(data).toMatchObject({
        status: 'healthy',
        version: '2.0.0',
        database: 'connected',
      })
      expect(data.timestamp).toBeDefined()
    })
  })

  describe('Orchestrator Registration', () => {
    it('should register a new orchestrator', async () => {
      const response = await fetch(`${baseUrl}/api/v1/orchestrators/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cluster: 'test',
          machine_id: 'test-machine-1',
          repo_url: 'https://github.com/test/repo',
          capabilities: { roles: ['implement', 'test'] },
          version: '2.0.0',
        }),
      })

      const data = await response.json()

      expect(response.status).toBe(200)
      expect(data.orchestrator_id).toBe('test-test-machine-1')
      expect(data.registered_at).toBeDefined()
    })

    it('should return existing orchestrator on re-registration', async () => {
      // First registration
      await fetch(`${baseUrl}/api/v1/orchestrators/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cluster: 'test',
          machine_id: 'test-machine-2',
          repo_url: 'https://github.com/test/repo',
        }),
      })

      // Second registration (should update, not create new)
      const response = await fetch(`${baseUrl}/api/v1/orchestrators/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cluster: 'test',
          machine_id: 'test-machine-2',
          repo_url: 'https://github.com/test/repo',
        }),
      })

      const data = await response.json()
      expect(response.status).toBe(200)
      expect(data.orchestrator_id).toBe('test-test-machine-2')
    })
  })

  describe('Heartbeat', () => {
    it('should accept heartbeat from registered orchestrator', async () => {
      // Register first
      await fetch(`${baseUrl}/api/v1/orchestrators/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cluster: 'test',
          machine_id: 'test-machine-3',
          repo_url: 'https://github.com/test/repo',
        }),
      })

      // Send heartbeat
      const response = await fetch(
        `${baseUrl}/api/v1/orchestrators/test-test-machine-3/heartbeat`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            timestamp: new Date().toISOString(),
          }),
        }
      )

      expect(response.status).toBe(200)
      const data = await response.json()
      expect(data.success).toBe(true)
    })
  })

  describe('Task CRUD Operations', () => {
    it('should create a new task', async () => {
      const taskId = `test-task-${Date.now()}`
      const response = await fetch(`${baseUrl}/api/v1/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: taskId,
          file_path: `tasks/incoming/${taskId}.md`,
          queue: 'incoming',
          priority: 'P1',
          role: 'implement',
          branch: 'main',
        }),
      })

      const data = await response.json()
      expect(response.status).toBe(201)
      expect(data.id).toBe(taskId)
      expect(data.queue).toBe('incoming')
      expect(data.priority).toBe('P1')
    })

    it('should list tasks', async () => {
      const response = await fetch(`${baseUrl}/api/v1/tasks`)
      const data = await response.json()

      expect(response.status).toBe(200)
      expect(data.tasks).toBeDefined()
      expect(Array.isArray(data.tasks)).toBe(true)
      expect(data.total).toBeGreaterThanOrEqual(0)
    })

    it('should filter tasks by queue', async () => {
      const response = await fetch(`${baseUrl}/api/v1/tasks?queue=incoming`)
      const data = await response.json()

      expect(response.status).toBe(200)
      expect(data.tasks).toBeDefined()
      data.tasks.forEach((task: any) => {
        expect(task.queue).toBe('incoming')
      })
    })

    it('should get a task by ID', async () => {
      // Create a task first
      const taskId = `test-task-get-${Date.now()}`
      await fetch(`${baseUrl}/api/v1/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: taskId,
          file_path: `tasks/incoming/${taskId}.md`,
          queue: 'incoming',
          priority: 'P2',
          role: 'test',
        }),
      })

      // Get the task
      const response = await fetch(`${baseUrl}/api/v1/tasks/${taskId}`)
      const data = await response.json()

      expect(response.status).toBe(200)
      expect(data.id).toBe(taskId)
      expect(data.priority).toBe('P2')
    })
  })

  describe('Task Lifecycle', () => {
    it('should claim a task', async () => {
      // Register orchestrator
      await fetch(`${baseUrl}/api/v1/orchestrators/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cluster: 'test',
          machine_id: 'claim-test',
          repo_url: 'https://github.com/test/repo',
        }),
      })

      // Create a task
      const taskId = `test-task-claim-${Date.now()}`
      await fetch(`${baseUrl}/api/v1/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: taskId,
          file_path: `tasks/incoming/${taskId}.md`,
          queue: 'incoming',
          priority: 'P1',
          role: 'implement',
        }),
      })

      // Claim the task
      const response = await fetch(`${baseUrl}/api/v1/tasks/claim`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          orchestrator_id: 'test-claim-test',
          agent_name: 'test-agent',
          role_filter: 'implement',
        }),
      })

      const data = await response.json()
      expect(response.status).toBe(200)
      expect(data.id).toBe(taskId)
      expect(data.queue).toBe('claimed')
      expect(data.claimed_by).toBe('test-claim-test')
      expect(data.lease_expires_at).toBeDefined()
    })

    it('should submit a claimed task', async () => {
      // Register orchestrator
      await fetch(`${baseUrl}/api/v1/orchestrators/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cluster: 'test',
          machine_id: 'submit-test',
          repo_url: 'https://github.com/test/repo',
        }),
      })

      // Create and claim a task
      const taskId = `test-task-submit-${Date.now()}`
      await fetch(`${baseUrl}/api/v1/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: taskId,
          file_path: `tasks/incoming/${taskId}.md`,
          queue: 'incoming',
          priority: 'P1',
          role: 'implement',
        }),
      })

      await fetch(`${baseUrl}/api/v1/tasks/claim`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          orchestrator_id: 'test-submit-test',
          agent_name: 'test-agent',
          role_filter: 'implement',
        }),
      })

      // Submit the task
      const response = await fetch(`${baseUrl}/api/v1/tasks/${taskId}/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          commits_count: 3,
          turns_used: 10,
          check_results: 'all passed',
        }),
      })

      const data = await response.json()
      expect(response.status).toBe(200)
      expect(data.queue).toBe('provisional')
      expect(data.commits_count).toBe(3)
      expect(data.submitted_at).toBeDefined()
    })

    it('should accept a provisional task', async () => {
      // Register orchestrator
      await fetch(`${baseUrl}/api/v1/orchestrators/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cluster: 'test',
          machine_id: 'accept-test',
          repo_url: 'https://github.com/test/repo',
        }),
      })

      // Create, claim, and submit a task
      const taskId = `test-task-accept-${Date.now()}`
      await fetch(`${baseUrl}/api/v1/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: taskId,
          file_path: `tasks/incoming/${taskId}.md`,
          queue: 'incoming',
          priority: 'P1',
          role: 'implement',
        }),
      })

      await fetch(`${baseUrl}/api/v1/tasks/claim`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          orchestrator_id: 'test-accept-test',
          agent_name: 'test-agent',
          role_filter: 'implement',
        }),
      })

      await fetch(`${baseUrl}/api/v1/tasks/${taskId}/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          commits_count: 2,
          turns_used: 5,
        }),
      })

      // Accept the task
      const response = await fetch(`${baseUrl}/api/v1/tasks/${taskId}/accept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          accepted_by: 'test-reviewer',
        }),
      })

      const data = await response.json()
      expect(response.status).toBe(200)
      expect(data.queue).toBe('done')
      expect(data.completed_at).toBeDefined()
    })

    it('should reject a provisional task', async () => {
      // Register orchestrator
      await fetch(`${baseUrl}/api/v1/orchestrators/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cluster: 'test',
          machine_id: 'reject-test',
          repo_url: 'https://github.com/test/repo',
        }),
      })

      // Create, claim, and submit a task
      const taskId = `test-task-reject-${Date.now()}`
      await fetch(`${baseUrl}/api/v1/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: taskId,
          file_path: `tasks/incoming/${taskId}.md`,
          queue: 'incoming',
          priority: 'P1',
          role: 'implement',
        }),
      })

      await fetch(`${baseUrl}/api/v1/tasks/claim`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          orchestrator_id: 'test-reject-test',
          agent_name: 'test-agent',
          role_filter: 'implement',
        }),
      })

      await fetch(`${baseUrl}/api/v1/tasks/${taskId}/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          commits_count: 1,
          turns_used: 3,
        }),
      })

      // Reject the task
      const response = await fetch(`${baseUrl}/api/v1/tasks/${taskId}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          reason: 'Tests failing',
          rejected_by: 'test-reviewer',
        }),
      })

      const data = await response.json()
      expect(response.status).toBe(200)
      expect(data.queue).toBe('incoming')
      expect(data.rejection_count).toBe(1)
    })
  })

  describe('Error Handling', () => {
    it('should return 404 for non-existent task', async () => {
      const response = await fetch(`${baseUrl}/api/v1/tasks/non-existent-task`)
      expect(response.status).toBe(404)
    })

    it('should return 400 for invalid task creation', async () => {
      const response = await fetch(`${baseUrl}/api/v1/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          // Missing required fields
          id: 'invalid-task',
        }),
      })
      expect(response.status).toBe(400)
    })

    it('should return 404 when claiming with no available tasks', async () => {
      const response = await fetch(`${baseUrl}/api/v1/tasks/claim`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          orchestrator_id: 'test-no-tasks',
          agent_name: 'test-agent',
          role_filter: 'non-existent-role',
        }),
      })
      expect(response.status).toBe(404)
    })
  })
})
