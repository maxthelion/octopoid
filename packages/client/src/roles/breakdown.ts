/**
 * Breakdown agent - breaks down large tasks into smaller subtasks
 */

import { BaseAgent, type AgentConfig } from './base-agent'
import { findTaskFile, parseTaskFile, createTaskFile, generateTaskId } from '../queue-utils'
import { createTask } from '../db-interface'

export class Breakdown extends BaseAgent {
  constructor(config: AgentConfig) {
    super(config)
  }

  /**
   * Main run loop
   */
  async run(): Promise<void> {
    this.log('Starting breakdown agent')

    try {
      // Claim a task needing breakdown
      const task = await this.claimNextTask('breakdown')
      if (!task) {
        this.log('No tasks available for breakdown')
        return
      }

      this.writeStatus(task.id, 'Analyzing task', 20)

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

      // Build breakdown prompt
      this.writeStatus(task.id, 'Breaking down into subtasks', 40)
      const prompt = this.buildBreakdownPrompt(taskContent)

      this.log('Analyzing task for breakdown...')
      const response = await this.callAnthropic(prompt, {
        model: this.config.model || 'claude-sonnet-4-20250514',
        maxTokens: 4096,
      })

      // Parse subtasks from response
      const subtasks = this.parseSubtasks(response)

      if (subtasks.length === 0) {
        this.log('No subtasks identified - task may be small enough already')
        await this.rejectTask(task.id, 'Task does not need breakdown')
        return
      }

      this.log(`Identified ${subtasks.length} subtasks`)
      this.writeStatus(task.id, 'Creating subtasks', 60)

      // Create subtasks
      const createdTasks: string[] = []
      for (let i = 0; i < subtasks.length; i++) {
        const subtask = subtasks[i]
        const subtaskId = generateTaskId()

        // Create task file
        createTaskFile('incoming', {
          id: subtaskId,
          title: subtask.title,
          role: 'implement',
          priority: task.priority,
          branch: task.branch,
          description: subtask.description,
          created_by: this.config.name,
          blocked_by: i > 0 ? createdTasks[i - 1] : undefined, // Chain dependencies
          project_id: task.project_id || undefined,
        })

        // Create task in database
        await createTask({
          id: subtaskId,
          file_path: `tasks/incoming/TASK-${subtaskId}.md`,
          queue: 'incoming',
          role: 'implement',
          priority: task.priority,
          branch: task.branch,
          blocked_by: i > 0 ? createdTasks[i - 1] : undefined,
          project_id: task.project_id || undefined,
        })

        createdTasks.push(subtaskId)
        this.log(`Created subtask ${i + 1}/${subtasks.length}: ${subtaskId}`)
      }

      // Mark original task as completed (breakdown done)
      this.writeStatus(task.id, 'Breakdown complete', 90)
      await this.submitTaskCompletion(task.id, 0, 1)
      await this.acceptTask(task.id)

      this.log(`Breakdown complete: ${createdTasks.length} subtasks created`)
    } catch (error) {
      this.error('Error in breakdown run', error as Error)
      if (this.currentTaskId) {
        await this.rejectTask(
          this.currentTaskId,
          `Breakdown error: ${(error as Error).message}`
        )
      }
    } finally {
      this.clearStatus()
    }
  }

  /**
   * Build breakdown prompt
   */
  private buildBreakdownPrompt(taskContent: {
    metadata: any
    body: string
  }): string {
    const { metadata, body } = taskContent

    return `You are an expert software architect analyzing a task for breakdown into smaller subtasks.

# Task: ${metadata.title}

${body}

## Your Mission

Analyze this task and determine if it should be broken down into smaller, more manageable subtasks.

**Guidelines:**
1. Each subtask should be completable in one focused session
2. Subtasks should have clear dependencies (what must be done first)
3. Each subtask should have a clear acceptance criteria
4. Don't over-break: if the task is already small enough (< 2 hours), don't break it down

## Output Format

If the task should be broken down, respond with a JSON array of subtasks:

\`\`\`json
[
  {
    "title": "Subtask 1 title",
    "description": "Detailed description of what needs to be done",
    "estimated_complexity": "S | M | L"
  },
  {
    "title": "Subtask 2 title",
    "description": "Detailed description (depends on subtask 1)",
    "estimated_complexity": "S | M | L"
  }
]
\`\`\`

If the task is small enough already, respond with:

\`\`\`json
[]
\`\`\`

Analyze the task now and provide your breakdown.`
  }

  /**
   * Parse subtasks from AI response
   */
  private parseSubtasks(response: string): Array<{
    title: string
    description: string
    complexity?: string
  }> {
    try {
      // Extract JSON from markdown code blocks
      const jsonMatch = response.match(/```json\s*([\s\S]*?)\s*```/)
      if (!jsonMatch) {
        this.error('No JSON found in response')
        return []
      }

      const json = JSON.parse(jsonMatch[1])

      if (!Array.isArray(json)) {
        this.error('Response is not an array')
        return []
      }

      return json.filter(
        (task: any) => task.title && task.description
      )
    } catch (error) {
      this.error('Failed to parse subtasks', error as Error)
      return []
    }
  }
}
