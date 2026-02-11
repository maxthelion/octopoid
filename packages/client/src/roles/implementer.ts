/**
 * Implementer agent - claims and implements tasks
 */

import { BaseAgent, type AgentConfig } from './base-agent'
import {
  createFeatureBranch,
  pushBranch,
  createPullRequest,
  getCommitCount,
  hasUncommittedChanges,
  commitChanges,
  getCurrentBranch,
} from '../git-utils'
import { findTaskFile, parseTaskFile } from '../queue-utils'

export class Implementer extends BaseAgent {
  constructor(config: AgentConfig) {
    super(config)
  }

  /**
   * Main run loop
   */
  async run(): Promise<void> {
    this.log('Starting implementer agent')

    try {
      // Claim a task
      const task = await this.claimNextTask('implement')
      if (!task) {
        this.log('No tasks available to claim')
        return
      }

      this.writeStatus(task.id, 'Setting up environment', 10)

      // Ensure task-specific worktree exists (for parallel execution)
      const worktreePath = await this.ensureTaskWorktree(task.id, task.branch)
      this.log(`Working in: ${worktreePath}`)

      // Create feature branch
      this.writeStatus(task.id, 'Creating feature branch', 20)
      const branchName = await createFeatureBranch(
        worktreePath,
        task.id,
        task.branch
      )
      this.log(`Created branch: ${branchName}`)

      // Get task description
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

      // Build implementation prompt
      this.writeStatus(task.id, 'Implementing solution', 30)
      const prompt = this.buildImplementationPrompt(taskContent)

      this.log('Invoking Claude Code for implementation...')
      const result = await this.invokeClaudeCode(prompt, {
        allowedTools: ['Read', 'Write', 'Bash', 'Grep', 'Glob'],
        maxTurns: this.config.maxTurns || 50,
      })

      if (result.exitCode !== 0) {
        this.error(`Claude Code failed with exit code: ${result.exitCode}`)
        await this.rejectTask(task.id, `Implementation failed: ${result.stderr}`)
        return
      }

      // Check if there are changes
      this.writeStatus(task.id, 'Checking for changes', 70)
      const hasChanges = await hasUncommittedChanges(worktreePath)

      if (!hasChanges) {
        this.log('No changes made, checking for existing commits')
        const commitCount = await getCommitCount(worktreePath, task.branch)

        if (commitCount === 0) {
          this.error('No changes or commits made')
          await this.rejectTask(task.id, 'No changes made during implementation')
          return
        }
      } else {
        // Commit changes
        this.writeStatus(task.id, 'Committing changes', 80)
        const currentBranch = await getCurrentBranch(worktreePath)
        const commitMessage = this.buildCommitMessage(taskContent, currentBranch)

        await commitChanges(worktreePath, commitMessage)
        this.log('Changes committed')
      }

      // Get final commit count
      const finalCommitCount = await getCommitCount(worktreePath, task.branch)
      this.log(`Total commits: ${finalCommitCount}`)

      // Push branch
      this.writeStatus(task.id, 'Pushing to remote', 85)
      const currentBranch = await getCurrentBranch(worktreePath)
      await pushBranch(worktreePath, currentBranch)
      this.log('Branch pushed to origin')

      // Create pull request
      this.writeStatus(task.id, 'Creating pull request', 90)
      const prUrl = await createPullRequest(
        worktreePath,
        currentBranch,
        task.branch,
        `[${task.id}] ${taskContent.metadata.title}`,
        this.buildPRBody(taskContent, finalCommitCount)
      )
      this.log(`Pull request created: ${prUrl}`)

      // Submit for review
      this.writeStatus(task.id, 'Submitting for review', 95)
      const turnsUsed = this.extractTurnsUsed(result.stdout)

      const submitted = await this.submitTaskCompletion(
        task.id,
        finalCommitCount,
        turnsUsed
      )

      if (submitted) {
        this.log(`Task ${task.id} submitted for review`)
        this.writeStatus(task.id, 'Submitted for review', 100)
      } else {
        this.error('Failed to submit task completion')
      }
    } catch (error) {
      this.error('Error in implementer run', error as Error)
      if (this.currentTaskId) {
        await this.rejectTask(
          this.currentTaskId,
          `Implementation error: ${(error as Error).message}`
        )
      }
    } finally {
      this.clearStatus()
    }
  }

  /**
   * Build implementation prompt for Claude Code
   */
  private buildImplementationPrompt(taskContent: {
    metadata: any
    body: string
    content: string
  }): string {
    const { metadata, body } = taskContent

    let prompt = `You are an expert software engineer implementing a task.

# Task: ${metadata.title}

`

    if (metadata.role) {
      prompt += `**Role:** ${metadata.role}\n`
    }

    if (metadata.priority) {
      prompt += `**Priority:** ${metadata.priority}\n`
    }

    prompt += `\n${body}\n\n`

    prompt += `## Your Mission

Implement this task following these guidelines:

1. **Read and understand** the existing codebase first
2. **Plan your approach** before making changes
3. **Write clean, maintainable code** that follows existing patterns
4. **Include tests** if appropriate
5. **Update documentation** as needed
6. **Commit your changes** with clear messages

## Constraints

- Follow the existing code style and patterns
- Don't break existing functionality
- Write self-documenting code with comments where needed
- Test your changes before committing

## Completion

When you're done:
1. Make sure all changes are committed
2. Verify nothing is broken
3. Your work will be automatically submitted for review

Begin implementation now.`

    return prompt
  }

  /**
   * Build commit message
   */
  private buildCommitMessage(
    taskContent: { metadata: any },
    branchName: string
  ): string {
    const { metadata } = taskContent
    return `feat: ${metadata.title}

Implemented via Octopoid agent
Task ID: ${metadata.id}
Branch: ${branchName}

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>`
  }

  /**
   * Build PR body
   */
  private buildPRBody(
    taskContent: { metadata: any; body: string },
    commitCount: number
  ): string {
    const { metadata, body } = taskContent

    let prBody = `## Summary

${metadata.title}

## Task Details

${body}

## Implementation Stats

- **Commits:** ${commitCount}
- **Agent:** ${this.config.name}
- **Task ID:** ${metadata.id}

## Checklist

- [ ] Code follows project standards
- [ ] Tests pass (if applicable)
- [ ] Documentation updated (if needed)
- [ ] No breaking changes

---

🤖 Generated by [Octopoid](https://github.com/org/octopoid) - AI-driven development orchestrator
`

    return prBody
  }

  /**
   * Extract turns used from Claude Code output
   */
  private extractTurnsUsed(stdout: string): number {
    // Try to extract turns from output
    // Format: "Used X turns" or similar
    const match = stdout.match(/used\s+(\d+)\s+turns?/i)
    if (match) {
      return parseInt(match[1], 10)
    }

    // Default estimate based on output length
    const outputLines = stdout.split('\n').length
    return Math.ceil(outputLines / 100)
  }
}
