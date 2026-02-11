/**
 * Base agent class for all agent roles
 * Provides common functionality for task claiming, Claude API integration, logging
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync, appendFileSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'
import Anthropic from '@anthropic-ai/sdk'
import type { Task, ClaimTaskRequest, SubmitTaskRequest, TaskRole } from '@octopoid/shared'
import { loadConfig, getRuntimeDir } from '../config'
import { claimTask, submitCompletion, acceptCompletion, rejectCompletion } from '../db-interface'
import { ensureWorktree, removeWorktree } from '../git-utils'

export interface AgentConfig {
  name: string
  role: TaskRole
  model?: string
  maxTurns?: number
  maxConcurrent?: number
  debug?: boolean
}

/**
 * Abstract base class for all agent roles
 */
export abstract class BaseAgent {
  protected config: AgentConfig
  protected worktreePath: string | null = null
  protected currentTaskId: string | null = null
  protected logFile: string | null = null
  protected anthropic: Anthropic

  constructor(config: AgentConfig) {
    this.config = config

    // Initialize Anthropic client
    const apiKey = process.env.ANTHROPIC_API_KEY
    if (!apiKey) {
      throw new Error('ANTHROPIC_API_KEY environment variable is required')
    }

    this.anthropic = new Anthropic({ apiKey })

    // Setup debug logging if enabled
    if (config.debug) {
      this.setupDebugLogging()
    }
  }

  /**
   * Setup debug logging to file
   */
  private setupDebugLogging(): void {
    const runtimeDir = getRuntimeDir()
    const logsDir = join(runtimeDir, '..', 'logs', 'agents')
    mkdirSync(logsDir, { recursive: true })

    const dateStr = new Date().toISOString().split('T')[0]
    this.logFile = join(logsDir, `${this.config.name}-${dateStr}.log`)
  }

  /**
   * Write debug log
   */
  protected debugLog(level: string, message: string): void {
    if (!this.logFile) return

    const timestamp = new Date().toISOString()
    const logLine = `[${timestamp}] [${level}] [${this.config.name}] ${message}\n`

    try {
      appendFileSync(this.logFile, logLine, 'utf-8')
    } catch {
      // Don't fail if we can't write logs
    }
  }

  /**
   * Log a message
   */
  protected log(message: string): void {
    console.error(`[${this.config.name}] ${message}`)
    if (this.config.debug) {
      this.debugLog('INFO', message)
    }
  }

  /**
   * Log a debug message
   */
  protected debug(message: string): void {
    if (this.config.debug) {
      this.debugLog('DEBUG', message)
    }
  }

  /**
   * Log an error
   */
  protected error(message: string, error?: Error): void {
    console.error(`[${this.config.name}] ERROR: ${message}`)
    if (error) {
      console.error(error)
    }
    if (this.config.debug) {
      this.debugLog('ERROR', `${message}${error ? `: ${error.message}` : ''}`)
    }
  }

  /**
   * Claim a task from the queue
   */
  protected async claimNextTask(roleFilter?: TaskRole | TaskRole[]): Promise<Task | null> {
    const config = loadConfig()
    const orchestratorId = config.server?.cluster && config.server?.machine_id
      ? `${config.server.cluster}-${config.server.machine_id}`
      : 'local'

    const request: ClaimTaskRequest = {
      orchestrator_id: orchestratorId,
      agent_name: this.config.name,
      role_filter: roleFilter || this.config.role,
    }

    try {
      const task = await claimTask(request)
      if (task) {
        this.currentTaskId = task.id
        this.log(`Claimed task: ${task.id}`)
        this.debug(`Task details: ${JSON.stringify(task, null, 2)}`)
      }
      return task
    } catch (error) {
      this.error('Failed to claim task', error as Error)
      return null
    }
  }

  /**
   * Submit task completion
   */
  protected async submitTaskCompletion(
    taskId: string,
    commitsCount: number,
    turnsUsed: number
  ): Promise<boolean> {
    try {
      const request: SubmitTaskRequest = {
        commits_count: commitsCount,
        turns_used: turnsUsed,
      }

      await submitCompletion(taskId, request)
      this.log(`Submitted completion for task: ${taskId}`)
      return true
    } catch (error) {
      this.error('Failed to submit completion', error as Error)
      return false
    }
  }

  /**
   * Accept task completion (for gatekeeper agents)
   */
  protected async acceptTask(taskId: string): Promise<boolean> {
    try {
      await acceptCompletion(taskId, {
        accepted_by: this.config.name,
        completed_at: new Date().toISOString(),
      })
      this.log(`Accepted task: ${taskId}`)
      return true
    } catch (error) {
      this.error('Failed to accept task', error as Error)
      return false
    }
  }

  /**
   * Reject task completion (for gatekeeper agents)
   */
  protected async rejectTask(taskId: string, reason: string): Promise<boolean> {
    try {
      await rejectCompletion(taskId, {
        reason,
        rejected_by: this.config.name,
      })
      this.log(`Rejected task: ${taskId} - ${reason}`)
      return true
    } catch (error) {
      this.error('Failed to reject task', error as Error)
      return false
    }
  }

  /**
   * Ensure worktree exists for the current task
   * Each task gets its own isolated worktree for parallel execution
   */
  protected async ensureTaskWorktree(taskId: string, baseBranch: string = 'main'): Promise<string> {
    this.worktreePath = await ensureWorktree(taskId, baseBranch)
    this.log(`Worktree ready for task ${taskId}: ${this.worktreePath}`)
    return this.worktreePath
  }

  /**
   * Remove worktree for the current task
   */
  protected async cleanupWorktree(taskId: string): Promise<void> {
    if (this.worktreePath) {
      await removeWorktree(taskId)
      this.worktreePath = null
      this.log(`Worktree cleaned up for task ${taskId}`)
    }
  }

  /**
   * Call Anthropic API with a message
   */
  protected async callAnthropic(
    prompt: string,
    options: {
      model?: string
      maxTokens?: number
      temperature?: number
      systemPrompt?: string
    } = {}
  ): Promise<string> {
    const {
      model = this.config.model || 'claude-sonnet-4-20250514',
      maxTokens = 8192,
      temperature = 1,
      systemPrompt,
    } = options

    this.debug(`Calling Anthropic API with model: ${model}`)
    this.debug(`Prompt length: ${prompt.length} chars`)

    try {
      const message = await this.anthropic.messages.create({
        model,
        max_tokens: maxTokens,
        temperature,
        system: systemPrompt,
        messages: [
          {
            role: 'user',
            content: prompt,
          },
        ],
      })

      const response = message.content[0]
      if (response.type === 'text') {
        this.debug(`Response length: ${response.text.length} chars`)
        return response.text
      }

      return ''
    } catch (error) {
      this.error('Anthropic API call failed', error as Error)
      throw error
    }
  }

  /**
   * Invoke Claude Code CLI (for agentic workflows)
   */
  protected async invokeClaudeCode(
    prompt: string,
    options: {
      allowedTools?: string[]
      maxTurns?: number
      timeout?: number
    } = {}
  ): Promise<{ exitCode: number; stdout: string; stderr: string }> {
    const { allowedTools, maxTurns = this.config.maxTurns || 50, timeout = 3600000 } = options

    const args = ['-p', prompt]

    if (allowedTools) {
      args.push('--allowedTools', allowedTools.join(','))
    }

    if (maxTurns) {
      args.push('--max-turns', String(maxTurns))
    }

    this.log(`Invoking Claude Code: claude ${args.slice(0, 2).join(' ')}...`)
    this.debug(`Working directory: ${this.worktreePath}`)
    this.debug(`Max turns: ${maxTurns}`)
    this.debug(`Prompt length: ${prompt.length} chars`)

    return new Promise((resolve, reject) => {
      const child = spawn('claude', args, {
        cwd: this.worktreePath || process.cwd(),
        env: {
          ...process.env,
          AGENT_NAME: this.config.name,
          AGENT_ROLE: this.config.role,
          CURRENT_TASK_ID: this.currentTaskId || '',
        },
        timeout,
      })

      let stdout = ''
      let stderr = ''

      child.stdout.on('data', (data) => {
        stdout += data.toString()
      })

      child.stderr.on('data', (data) => {
        stderr += data.toString()
      })

      child.on('close', (code) => {
        this.debug(`Claude Code exit code: ${code}`)
        this.debug(`Stdout length: ${stdout.length} chars`)
        this.debug(`Stderr length: ${stderr.length} chars`)

        if (code !== 0) {
          this.debug(`Stderr: ${stderr.substring(0, 1000)}`)
        }

        resolve({ exitCode: code || 0, stdout, stderr })
      })

      child.on('error', (error) => {
        this.error('Failed to invoke Claude Code', error)
        reject(error)
      })
    })
  }

  /**
   * Write agent status (for monitoring)
   */
  protected writeStatus(
    taskId: string,
    currentSubtask: string,
    progressPercent: number,
    taskTitle?: string
  ): void {
    const runtimeDir = getRuntimeDir()
    const statusDir = join(runtimeDir, '..', 'agents', this.config.name)
    mkdirSync(statusDir, { recursive: true })

    const statusPath = join(statusDir, 'status.json')
    const status = {
      task_id: taskId,
      task_title: taskTitle || '',
      current_subtask: currentSubtask,
      progress_percent: Math.min(100, Math.max(0, progressPercent)),
      last_updated: new Date().toISOString(),
      agent_name: this.config.name,
    }

    writeFileSync(statusPath, JSON.stringify(status, null, 2), 'utf-8')
  }

  /**
   * Clear agent status
   */
  protected clearStatus(): void {
    const runtimeDir = getRuntimeDir()
    const statusPath = join(
      runtimeDir,
      '..',
      'agents',
      this.config.name,
      'status.json'
    )

    if (existsSync(statusPath)) {
      unlinkSync(statusPath)
    }
  }

  /**
   * Main run method - implemented by subclasses
   */
  abstract run(): Promise<void>

  /**
   * Cleanup method - called when agent shuts down
   */
  async cleanup(): Promise<void> {
    this.clearStatus()
    if (this.currentTaskId) {
      await this.cleanupWorktree(this.currentTaskId)
    }
  }
}
