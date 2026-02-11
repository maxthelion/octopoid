/**
 * Client Integration Tests
 * Tests client-server communication and orchestrator functionality
 */

import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { OctopoidAPIClient } from '../src/api-client'
import type { Task } from '@octopoid/shared'

describe('Client Integration Tests', () => {
  let client: OctopoidAPIClient
  let testDir: string
  const serverUrl = process.env.TEST_SERVER_URL || 'http://localhost:8787'

  beforeAll(() => {
    client = new OctopoidAPIClient(serverUrl)
    testDir = join(tmpdir(), `octopoid-test-${Date.now()}`)
    mkdirSync(testDir, { recursive: true })
  })

  afterAll(() => {
    rmSync(testDir, { recursive: true, force: true })
  })

  describe('API Client', () => {
    it('should check server health', async () => {
      const health = await client.healthCheck()
      expect(health.status).toBe('healthy')
      expect(health.version).toBe('2.0.0')
      expect(health.database).toBe('connected')
    })

    it('should register an orchestrator', async () => {
      const response = await client.registerOrchestrator({
        cluster: 'test-client',
        machine_id: `test-${Date.now()}`,
        repo_url: 'https://github.com/test/repo',
        capabilities: { roles: ['implement'] },
        version: '2.0.0',
      })

      expect(response.orchestrator_id).toBeDefined()
      expect(response.registered_at).toBeDefined()
    })

    it('should send heartbeat', async () => {
      // Register first
      const registration = await client.registerOrchestrator({
        cluster: 'test-client',
        machine_id: `heartbeat-${Date.now()}`,
        repo_url: 'https://github.com/test/repo',
      })

      // Send heartbeat
      const response = await client.sendHeartbeat(
        registration.orchestrator_id,
        { timestamp: new Date().toISOString() }
      )

      expect(response.success).toBe(true)
    })
  })

  describe('Task Operations', () => {
    let testTaskId: string

    beforeEach(() => {
      testTaskId = `test-task-${Date.now()}`
    })

    it('should create a task', async () => {
      const task = await client.createTask({
        id: testTaskId,
        file_path: `tasks/incoming/${testTaskId}.md`,
        queue: 'incoming',
        priority: 'P1',
        role: 'implement',
      })

      expect(task).toBeDefined()
      expect(task!.id).toBe(testTaskId)
      expect(task!.queue).toBe('incoming')
    })

    it('should list tasks', async () => {
      // Create a test task first
      await client.createTask({
        id: testTaskId,
        file_path: `tasks/incoming/${testTaskId}.md`,
        queue: 'incoming',
        priority: 'P2',
        role: 'test',
      })

      const response = await client.listTasks()
      expect(response.tasks).toBeDefined()
      expect(Array.isArray(response.tasks)).toBe(true)
      expect(response.total).toBeGreaterThan(0)
    })

    it('should filter tasks by queue', async () => {
      await client.createTask({
        id: testTaskId,
        file_path: `tasks/incoming/${testTaskId}.md`,
        queue: 'incoming',
        priority: 'P1',
        role: 'implement',
      })

      const response = await client.listTasks({ queue: 'incoming' })
      response.tasks.forEach(task => {
        expect(task.queue).toBe('incoming')
      })
    })

    it('should get a specific task', async () => {
      await client.createTask({
        id: testTaskId,
        file_path: `tasks/incoming/${testTaskId}.md`,
        queue: 'incoming',
        priority: 'P1',
        role: 'implement',
      })

      const task = await client.getTask(testTaskId)
      expect(task).toBeDefined()
      expect(task!.id).toBe(testTaskId)
    })

    it('should claim a task', async () => {
      // Register orchestrator
      const registration = await client.registerOrchestrator({
        cluster: 'test-client',
        machine_id: `claim-${Date.now()}`,
        repo_url: 'https://github.com/test/repo',
      })

      // Create task
      await client.createTask({
        id: testTaskId,
        file_path: `tasks/incoming/${testTaskId}.md`,
        queue: 'incoming',
        priority: 'P1',
        role: 'implement',
      })

      // Claim task
      const task = await client.claimTask({
        orchestrator_id: registration.orchestrator_id,
        agent_name: 'test-agent',
        role_filter: 'implement',
      })

      expect(task).toBeDefined()
      expect(task!.queue).toBe('claimed')
      expect(task!.claimed_by).toBe(registration.orchestrator_id)
    })

    it('should handle full task lifecycle', async () => {
      // Register orchestrator
      const registration = await client.registerOrchestrator({
        cluster: 'test-client',
        machine_id: `lifecycle-${Date.now()}`,
        repo_url: 'https://github.com/test/repo',
      })

      // Create task
      await client.createTask({
        id: testTaskId,
        file_path: `tasks/incoming/${testTaskId}.md`,
        queue: 'incoming',
        priority: 'P1',
        role: 'implement',
      })

      // Claim task
      let task = await client.claimTask({
        orchestrator_id: registration.orchestrator_id,
        agent_name: 'test-agent',
        role_filter: 'implement',
      })
      expect(task!.queue).toBe('claimed')

      // Submit task
      task = await client.submitCompletion(testTaskId, {
        commits_count: 3,
        turns_used: 10,
      })
      expect(task.queue).toBe('provisional')
      expect(task.commits_count).toBe(3)

      // Accept task
      task = await client.acceptCompletion(testTaskId, {
        accepted_by: 'test-reviewer',
      })
      expect(task.queue).toBe('done')
      expect(task.completed_at).toBeDefined()
    })

    it('should handle task rejection', async () => {
      // Register orchestrator
      const registration = await client.registerOrchestrator({
        cluster: 'test-client',
        machine_id: `reject-${Date.now()}`,
        repo_url: 'https://github.com/test/repo',
      })

      // Create, claim, and submit task
      await client.createTask({
        id: testTaskId,
        file_path: `tasks/incoming/${testTaskId}.md`,
        queue: 'incoming',
        priority: 'P1',
        role: 'implement',
      })

      await client.claimTask({
        orchestrator_id: registration.orchestrator_id,
        agent_name: 'test-agent',
        role_filter: 'implement',
      })

      await client.submitCompletion(testTaskId, {
        commits_count: 1,
        turns_used: 5,
      })

      // Reject task
      const task = await client.rejectCompletion(testTaskId, {
        reason: 'Tests failing',
        rejected_by: 'test-reviewer',
      })

      expect(task.queue).toBe('incoming')
      expect(task.rejection_count).toBe(1)
    })
  })

  describe('Offline Mode Simulation', () => {
    it('should handle network errors gracefully', async () => {
      const offlineClient = new OctopoidAPIClient('http://localhost:9999', {
        timeout: 1000,
      })

      await expect(async () => {
        await offlineClient.healthCheck()
      }).rejects.toThrow()
    })
  })

  describe('Concurrent Operations', () => {
    it('should handle multiple orchestrators claiming different tasks', async () => {
      // Register two orchestrators
      const reg1 = await client.registerOrchestrator({
        cluster: 'test-client',
        machine_id: `concurrent-1-${Date.now()}`,
        repo_url: 'https://github.com/test/repo',
      })

      const reg2 = await client.registerOrchestrator({
        cluster: 'test-client',
        machine_id: `concurrent-2-${Date.now()}`,
        repo_url: 'https://github.com/test/repo',
      })

      // Create two tasks
      const task1Id = `concurrent-task-1-${Date.now()}`
      const task2Id = `concurrent-task-2-${Date.now()}`

      await client.createTask({
        id: task1Id,
        file_path: `tasks/incoming/${task1Id}.md`,
        queue: 'incoming',
        priority: 'P1',
        role: 'implement',
      })

      await client.createTask({
        id: task2Id,
        file_path: `tasks/incoming/${task2Id}.md`,
        queue: 'incoming',
        priority: 'P1',
        role: 'implement',
      })

      // Both claim tasks concurrently
      const [claim1, claim2] = await Promise.all([
        client.claimTask({
          orchestrator_id: reg1.orchestrator_id,
          agent_name: 'agent-1',
          role_filter: 'implement',
        }),
        client.claimTask({
          orchestrator_id: reg2.orchestrator_id,
          agent_name: 'agent-2',
          role_filter: 'implement',
        }),
      ])

      // Both should get different tasks
      expect(claim1).toBeDefined()
      expect(claim2).toBeDefined()
      expect(claim1!.id).not.toBe(claim2!.id)
    })
  })

  describe('Error Handling', () => {
    it('should handle invalid task ID', async () => {
      const task = await client.getTask('non-existent-task')
      expect(task).toBeNull()
    })

    it('should handle claim with no available tasks', async () => {
      const task = await client.claimTask({
        orchestrator_id: 'test-no-tasks',
        agent_name: 'test-agent',
        role_filter: 'implement',
      })
      expect(task).toBeNull()
    })
  })
})
