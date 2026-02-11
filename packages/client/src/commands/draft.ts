/**
 * Draft management commands
 * Drafts represent ideas/proposals that can be converted to tasks or projects
 */

import { Command } from 'commander'
import type {
  Draft,
  CreateDraftRequest,
  UpdateDraftRequest,
  DraftStatus,
} from '@octopoid/shared'
import { OctopoidAPIClient } from '../api-client'
import { loadConfig } from '../config'

export function registerDraftCommands(program: Command): void {
  const draft = program
    .command('draft')
    .description('Manage draft documents (ideas, proposals)')

  /**
   * octopoid draft create <title>
   */
  draft
    .command('create')
    .description('Create a new draft')
    .argument('<title>', 'Draft title')
    .option('-a, --author <author>', 'Author name (defaults to git user)')
    .option('-s, --status <status>', 'Initial status', 'idea')
    .option('-d, --domain <domain>', 'Domain/category')
    .option('-t, --tags <tags>', 'Comma-separated tags')
    .option('--file <path>', 'Path to draft markdown file')
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

        // Get author from git config if not provided
        let author = options.author
        if (!author) {
          try {
            const { execSync } = await import('node:child_process')
            author = execSync('git config user.name', { encoding: 'utf-8' }).trim()
          } catch {
            author = 'unknown'
          }
        }

        const request: CreateDraftRequest = {
          id,
          title,
          author,
          status: options.status as DraftStatus,
          domain: options.domain,
          file_path: options.file,
          tags: options.tags ? options.tags.split(',').map((t: string) => t.trim()) : undefined,
        }

        const response = await client.request<Draft>(
          'POST',
          '/api/v1/drafts',
          request
        )

        if (!response) {
          console.error('✗ Failed to create draft')
          process.exit(1)
        }

        console.log('✓ Draft created')
        console.log(`  ID:     ${response.id}`)
        console.log(`  Title:  ${response.title}`)
        console.log(`  Status: ${response.status}`)
        console.log(`  Author: ${response.author}`)
        if (response.domain) {
          console.log(`  Domain: ${response.domain}`)
        }
      } catch (error) {
        console.error('Error creating draft:', (error as Error).message)
        process.exit(1)
      }
    })

  /**
   * octopoid draft list
   */
  draft
    .command('list')
    .description('List all drafts')
    .option('-s, --status <status>', 'Filter by status')
    .option('-a, --author <author>', 'Filter by author')
    .option('-d, --domain <domain>', 'Filter by domain')
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
        if (options.author) params.set('author', options.author)
        if (options.domain) params.set('domain', options.domain)
        params.set('limit', options.limit)

        const response = await client.request<{
          drafts: Draft[]
          total: number
        }>('GET', `/api/v1/drafts?${params.toString()}`)

        if (!response || !response.drafts) {
          console.log('No drafts found')
          return
        }

        console.log(`Found ${response.drafts.length} draft(s) (total: ${response.total})`)
        console.log()

        for (const draft of response.drafts) {
          console.log(`[${draft.status.toUpperCase()}] ${draft.id}`)
          console.log(`  ${draft.title}`)
          console.log(`  Author: ${draft.author}`)
          if (draft.domain) {
            console.log(`  Domain: ${draft.domain}`)
          }
          if (draft.tags) {
            const tags = JSON.parse(draft.tags)
            console.log(`  Tags: ${tags.join(', ')}`)
          }
          console.log()
        }
      } catch (error) {
        console.error('Error listing drafts:', (error as Error).message)
        process.exit(1)
      }
    })

  /**
   * octopoid draft show <id>
   */
  draft
    .command('show')
    .description('Show draft details')
    .argument('<id>', 'Draft ID')
    .action(async (id) => {
      try {
        const config = loadConfig()
        if (!config.server?.url) {
          console.error('Error: Server URL not configured')
          process.exit(1)
        }

        const client = new OctopoidAPIClient(config.server.url)
        const draft = await client.request<Draft>('GET', `/api/v1/drafts/${id}`)

        if (!draft) {
          console.error(`✗ Draft ${id} not found`)
          process.exit(1)
        }

        console.log('Draft Details')
        console.log('='.repeat(50))
        console.log(`ID:         ${draft.id}`)
        console.log(`Title:      ${draft.title}`)
        console.log(`Status:     ${draft.status}`)
        console.log(`Author:     ${draft.author}`)
        if (draft.domain) {
          console.log(`Domain:     ${draft.domain}`)
        }
        if (draft.file_path) {
          console.log(`File:       ${draft.file_path}`)
        }
        if (draft.linked_task_id) {
          console.log(`Task:       ${draft.linked_task_id}`)
        }
        if (draft.linked_project_id) {
          console.log(`Project:    ${draft.linked_project_id}`)
        }
        if (draft.tags) {
          const tags = JSON.parse(draft.tags)
          console.log(`Tags:       ${tags.join(', ')}`)
        }
        console.log(`Created:    ${draft.created_at}`)
        console.log(`Updated:    ${draft.updated_at}`)
      } catch (error) {
        console.error('Error showing draft:', (error as Error).message)
        process.exit(1)
      }
    })

  /**
   * octopoid draft update <id>
   */
  draft
    .command('update')
    .description('Update draft fields')
    .argument('<id>', 'Draft ID')
    .option('-s, --status <status>', 'New status')
    .option('-t, --title <title>', 'New title')
    .option('-d, --domain <domain>', 'New domain')
    .option('--link-task <task_id>', 'Link to task')
    .option('--link-project <project_id>', 'Link to project')
    .action(async (id, options) => {
      try {
        const config = loadConfig()
        if (!config.server?.url) {
          console.error('Error: Server URL not configured')
          process.exit(1)
        }

        const client = new OctopoidAPIClient(config.server.url)

        const request: UpdateDraftRequest = {}
        if (options.status) request.status = options.status as DraftStatus
        if (options.title) request.title = options.title
        if (options.domain) request.domain = options.domain
        if (options.linkTask) request.linked_task_id = options.linkTask
        if (options.linkProject) request.linked_project_id = options.linkProject

        if (Object.keys(request).length === 0) {
          console.error('Error: No fields to update')
          console.error('Use --status, --title, --domain, --link-task, or --link-project')
          process.exit(1)
        }

        const draft = await client.request<Draft>(
          'PATCH',
          `/api/v1/drafts/${id}`,
          request
        )

        if (!draft) {
          console.error(`✗ Failed to update draft ${id}`)
          process.exit(1)
        }

        console.log(`✓ Draft ${id} updated`)
        console.log(`  Title:  ${draft.title}`)
        console.log(`  Status: ${draft.status}`)
      } catch (error) {
        console.error('Error updating draft:', (error as Error).message)
        process.exit(1)
      }
    })

  /**
   * octopoid draft delete <id>
   */
  draft
    .command('delete')
    .description('Delete a draft')
    .argument('<id>', 'Draft ID')
    .option('-f, --force', 'Skip confirmation')
    .action(async (id, options) => {
      try {
        const config = loadConfig()
        if (!config.server?.url) {
          console.error('Error: Server URL not configured')
          process.exit(1)
        }

        if (!options.force) {
          // Simple confirmation (would use readline in production)
          console.log(`Are you sure you want to delete draft ${id}?`)
          console.log('Run with --force to skip this confirmation')
          process.exit(0)
        }

        const client = new OctopoidAPIClient(config.server.url)
        await client.request('DELETE', `/api/v1/drafts/${id}`)

        console.log(`✓ Draft ${id} deleted`)
      } catch (error) {
        console.error('Error deleting draft:', (error as Error).message)
        process.exit(1)
      }
    })
}
