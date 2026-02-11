/**
 * Gatekeeper agent - reviews completed tasks and accepts/rejects them
 */

import type { Task } from '@octopoid/shared'
import { BaseAgent, type AgentConfig } from './base-agent'
import { findTaskFile, parseTaskFile } from '../queue-utils'
import { listTasks } from '../db-interface'
import {
  checkoutBranch,
  pull,
  getDiff,
  getCommitMessages,
} from '../git-utils'

export class Gatekeeper extends BaseAgent {
  constructor(config: AgentConfig) {
    super(config)
  }

  /**
   * Main run loop
   */
  async run(): Promise<void> {
    this.log('Starting gatekeeper agent')

    try {
      // Get tasks in provisional queue (awaiting review)
      const provisionalTasks = await listTasks({
        queue: 'provisional',
        limit: 10,
      })

      if (provisionalTasks.length === 0) {
        this.log('No tasks awaiting review')
        return
      }

      // Review the highest priority task
      const task = provisionalTasks[0]
      this.log(`Reviewing task: ${task.id}`)

      this.writeStatus(task.id, 'Fetching changes', 20)

      // Ensure worktree and checkout branch
      const worktreePath = await this.ensureAgentWorktree(task.branch)

      // Get task content
      const taskFile = findTaskFile(task.id)
      if (!taskFile) {
        this.error(`Task file not found for ${task.id}`)
        await this.rejectTask(task.id, 'Task file not found')
        return
      }

      const taskContent = parseTaskFile(taskFile)
      if (!taskContent) {
        this.error(`Failed to parse task file for ${task.id}`)
        await this.rejectTask(task.id, 'Failed to parse task file')
        return
      }

      // Try to find and checkout the task branch
      // Branch name should be in PR URL or we need to derive it
      const branchName = this.deriveBranchName(task)
      if (!branchName) {
        this.error(`Cannot determine branch name for task ${task.id}`)
        await this.rejectTask(task.id, 'Cannot determine branch name')
        return
      }

      try {
        this.writeStatus(task.id, 'Checking out branch', 30)
        await checkoutBranch(worktreePath, branchName)
        await pull(worktreePath, 'origin', branchName)
      } catch (error) {
        this.error(`Failed to checkout branch ${branchName}`, error as Error)
        await this.rejectTask(task.id, `Cannot checkout branch: ${branchName}`)
        return
      }

      // Get diff and commit messages
      this.writeStatus(task.id, 'Analyzing changes', 50)
      const diff = await getDiff(worktreePath, task.branch, 'HEAD')
      const commitMessages = await getCommitMessages(worktreePath, task.branch)

      // Build review prompt
      const prompt = this.buildReviewPrompt(taskContent, diff, commitMessages)

      this.log('Reviewing implementation...')
      const response = await this.callAnthropic(prompt, {
        model: this.config.model || 'claude-opus-4-20250514', // Use opus for reviews
        maxTokens: 4096,
      })

      // Parse review decision
      const decision = this.parseReviewDecision(response)

      if (decision.approved) {
        this.log(`✅ Task ${task.id} APPROVED`)
        await this.acceptTask(task.id)
        this.writeStatus(task.id, 'Approved', 100)
      } else {
        this.log(`❌ Task ${task.id} REJECTED: ${decision.reason}`)
        await this.rejectTask(task.id, decision.reason || 'Failed review')
        this.writeStatus(task.id, 'Rejected', 100)
      }
    } catch (error) {
      this.error('Error in gatekeeper run', error as Error)
      if (this.currentTaskId) {
        await this.rejectTask(
          this.currentTaskId,
          `Review error: ${(error as Error).message}`
        )
      }
    } finally {
      this.clearStatus()
    }
  }

  /**
   * Derive branch name from task
   */
  private deriveBranchName(task: Task): string | null {
    // Try to extract from PR URL
    if (task.pr_url) {
      // GitHub PR URL format: https://github.com/user/repo/pull/123
      // We need to use gh CLI or API to get branch name
      // For now, use agent/{task_id} pattern
      return `agent/${task.id}`
    }

    // Default pattern
    return `agent/${task.id}`
  }

  /**
   * Build review prompt
   */
  private buildReviewPrompt(
    taskContent: { metadata: any; body: string },
    diff: string,
    commitMessages: string[]
  ): string {
    const { metadata, body } = taskContent

    return `You are an expert code reviewer examining a completed implementation.

# Task: ${metadata.title}

## Task Requirements

${body}

## Commit Messages

${commitMessages.map((msg, i) => `${i + 1}. ${msg}`).join('\n')}

## Code Changes

\`\`\`diff
${diff.length > 10000 ? diff.substring(0, 10000) + '\n\n... (diff truncated)' : diff}
\`\`\`

## Your Mission

Review this implementation and determine if it should be accepted or rejected.

**Review Criteria:**
1. ✅ Does the implementation fulfill the task requirements?
2. ✅ Is the code quality acceptable? (readability, maintainability)
3. ✅ Are there any obvious bugs or issues?
4. ✅ Does it follow existing code patterns?
5. ✅ Are tests included (if needed)?
6. ✅ Is documentation updated (if needed)?

**Decision Guidelines:**
- **ACCEPT** if the implementation meets requirements and is production-ready
- **REJECT** if there are significant issues that need to be fixed

## Output Format

Respond with a JSON object:

\`\`\`json
{
  "approved": true | false,
  "reason": "Brief explanation of decision (required if rejected)",
  "feedback": "Detailed feedback for the developer",
  "suggestions": "Optional suggestions for improvement"
}
\`\`\`

Perform your review now.`
  }

  /**
   * Parse review decision from AI response
   */
  private parseReviewDecision(response: string): {
    approved: boolean
    reason?: string
    feedback?: string
  } {
    try {
      // Extract JSON from markdown code blocks
      const jsonMatch = response.match(/```json\s*([\s\S]*?)\s*```/)
      if (!jsonMatch) {
        this.error('No JSON found in response')
        return { approved: false, reason: 'Invalid review response format' }
      }

      const json = JSON.parse(jsonMatch[1])

      return {
        approved: json.approved === true,
        reason: json.reason,
        feedback: json.feedback,
      }
    } catch (error) {
      this.error('Failed to parse review decision', error as Error)
      return { approved: false, reason: 'Failed to parse review response' }
    }
  }
}
