/**
 * Project management commands
 * Projects are containers for related tasks with shared context
 */

import { Command } from 'commander'
import type {
  Project,
  CreateProjectRequest,
  UpdateProjectRequest,
  ProjectStatus,
  Task,
} from '@octopoid/shared'
import { OctopoidAPIClient } from '../api-client'
import { loadConfig } from '../config'

export function registerProjectCommands(program: Command): void {
  const project = program
    .command('project')
    .description('Manage multi-task projects')

  /**
   * octopoid project create <title>
   */
  project
    .command('create')
    .description('Create a new project')
    .argument('<title>', 'Project title')
    .option('-d, --description <desc>', 'Project description')
    .option('-s, --status <status>', 'Initial status', 'draft')
    .option('-b, --branch <branch>', 'Feature branch name')
    .option('--base <base>', 'Base branch', 'main')
    .option('--auto-accept', 'Auto-accept all tasks (skip gatekeeper)')
    .option('--created-by <name>', 'Creator name')
    .action(async (title, options) => {
      try {
        const config = loadConfig()
        if (!config.server?.url) {
          console.error('Error: Server URL not configured')
          console.error('Run: octopoid init --server <url>')
          process.exit(1)
        }

        const client = new OctopoidAPIClient(config.server.url)

        // Generate ID from title
        const id = title
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, '-')
          .replace(/^-+|-+$/g, '')
          .substring(0, 50)

        const request: CreateProjectRequest = {
          id,
          title,
          description: options.description,
          status: options.status as ProjectStatus,
          branch: options.branch,
          base_branch: options.base,
          auto_accept: options.autoAccept || false,
          created_by: options.createdBy,
        }

        const response = await client.request<Project>(
          'POST',
          '/api/v1/projects',
          request
        )

        if (!response) {
          console.error('✗ Failed to create project')
          process.exit(1)
        }

        console.log('✓ Project created')
        console.log(`  ID:          ${response.id}`)
        console.log(`  Title:       ${response.title}`)
        console.log(`  Status:      ${response.status}`)
        console.log(`  Base Branch: ${response.base_branch}`)
        if (response.auto_accept) {
          console.log('  Auto-Accept: enabled (tasks skip gatekeeper)')
        }
      } catch (error) {
        console.error('Error creating project:', (error as Error).message)
        process.exit(1)
      }
    })

  /**
   * octopoid project list
   */
  project
    .command('list')
    .description('List all projects')
    .option('-s, --status <status>', 'Filter by status')
    .option('--created-by <name>', 'Filter by creator')
    .option('--limit <n>', 'Limit results', '50')
    .action(async (options) => {
      try {
        const config = loadConfig()
        if (!config.server?.url) {
          console.error('Error: Server URL not configured')
          process.exit(1)
        }

        const client = new OctopoidAPIClient(config.server.url)

        // Build query string
        const params = new URLSearchParams()
        if (options.status) params.set('status', options.status)
        if (options.createdBy) params.set('created_by', options.createdBy)
        params.set('limit', options.limit)

        const response = await client.request<{
          projects: Project[]
          total: number
        }>('GET', `/api/v1/projects?${params.toString()}`)

        if (!response || !response.projects) {
          console.log('No projects found')
          return
        }

        console.log(`Found ${response.projects.length} project(s) (total: ${response.total})`)
        console.log()

        for (const proj of response.projects) {
          console.log(`[${proj.status.toUpperCase()}] ${proj.id}`)
          console.log(`  ${proj.title}`)
          if (proj.description) {
            console.log(`  ${proj.description}`)
          }
          console.log(`  Base: ${proj.base_branch}`)
          if (proj.auto_accept) {
            console.log('  🚀 Auto-Accept Enabled')
          }
          console.log()
        }
      } catch (error) {
        console.error('Error listing projects:', (error as Error).message)
        process.exit(1)
      }
    })

  /**
   * octopoid project show <id>
   */
  project
    .command('show')
    .description('Show project details and tasks')
    .argument('<id>', 'Project ID')
    .action(async (id) => {
      try {
        const config = loadConfig()
        if (!config.server?.url) {
          console.error('Error: Server URL not configured')
          process.exit(1)
        }

        const client = new OctopoidAPIClient(config.server.url)

        // Get project details
        const proj = await client.request<Project>('GET', `/api/v1/projects/${id}`)

        if (!proj) {
          console.error(`✗ Project ${id} not found`)
          process.exit(1)
        }

        console.log('Project Details')
        console.log('='.repeat(50))
        console.log(`ID:          ${proj.id}`)
        console.log(`Title:       ${proj.title}`)
        if (proj.description) {
          console.log(`Description: ${proj.description}`)
        }
        console.log(`Status:      ${proj.status}`)
        console.log(`Base Branch: ${proj.base_branch}`)
        if (proj.branch) {
          console.log(`Branch:      ${proj.branch}`)
        }
        console.log(`Auto-Accept: ${proj.auto_accept ? 'enabled' : 'disabled'}`)
        if (proj.created_by) {
          console.log(`Created By:  ${proj.created_by}`)
        }
        console.log(`Created:     ${proj.created_at}`)
        if (proj.completed_at) {
          console.log(`Completed:   ${proj.completed_at}`)
        }

        // Get project tasks
        const tasksResp = await client.request<{
          tasks: Task[]
          total: number
        }>('GET', `/api/v1/projects/${id}/tasks`)

        if (tasksResp && tasksResp.tasks.length > 0) {
          console.log()
          console.log('Tasks')
          console.log('-'.repeat(50))
          for (const task of tasksResp.tasks) {
            console.log(`[${task.priority}] ${task.id} - ${task.queue}`)
            if (task.role) {
              console.log(`  Role: ${task.role}`)
            }
          }
          console.log()
          console.log(`Total: ${tasksResp.total} task(s)`)
        } else {
          console.log()
          console.log('No tasks in this project yet')
        }
      } catch (error) {
        console.error('Error showing project:', (error as Error).message)
        process.exit(1)
      }
    })

  /**
   * octopoid project update <id>
   */
  project
    .command('update')
    .description('Update project fields')
    .argument('<id>', 'Project ID')
    .option('-s, --status <status>', 'New status')
    .option('-t, --title <title>', 'New title')
    .option('-d, --description <desc>', 'New description')
    .option('-b, --branch <branch>', 'New branch name')
    .option('--auto-accept <bool>', 'Enable/disable auto-accept (true/false)')
    .action(async (id, options) => {
      try {
        const config = loadConfig()
        if (!config.server?.url) {
          console.error('Error: Server URL not configured')
          process.exit(1)
        }

        const client = new OctopoidAPIClient(config.server.url)

        const request: UpdateProjectRequest = {}
        if (options.status) request.status = options.status as ProjectStatus
        if (options.title) request.title = options.title
        if (options.description) request.description = options.description
        if (options.branch) request.branch = options.branch
        if (options.autoAccept !== undefined) {
          request.auto_accept = options.autoAccept === 'true'
        }

        if (Object.keys(request).length === 0) {
          console.error('Error: No fields to update')
          console.error('Use --status, --title, --description, --branch, or --auto-accept')
          process.exit(1)
        }

        const proj = await client.request<Project>(
          'PATCH',
          `/api/v1/projects/${id}`,
          request
        )

        if (!proj) {
          console.error(`✗ Failed to update project ${id}`)
          process.exit(1)
        }

        console.log(`✓ Project ${id} updated`)
        console.log(`  Title:  ${proj.title}`)
        console.log(`  Status: ${proj.status}`)
      } catch (error) {
        console.error('Error updating project:', (error as Error).message)
        process.exit(1)
      }
    })

  /**
   * octopoid project delete <id>
   */
  project
    .command('delete')
    .description('Delete a project')
    .argument('<id>', 'Project ID')
    .option('-f, --force', 'Skip confirmation')
    .action(async (id, options) => {
      try {
        const config = loadConfig()
        if (!config.server?.url) {
          console.error('Error: Server URL not configured')
          process.exit(1)
        }

        if (!options.force) {
          console.log(`Are you sure you want to delete project ${id}?`)
          console.log('Run with --force to skip this confirmation')
          process.exit(0)
        }

        const client = new OctopoidAPIClient(config.server.url)
        await client.request('DELETE', `/api/v1/projects/${id}`)

        console.log(`✓ Project ${id} deleted`)
      } catch (error) {
        console.error('Error deleting project:', (error as Error).message)
        process.exit(1)
      }
    })
}
