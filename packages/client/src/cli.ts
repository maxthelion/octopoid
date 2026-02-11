#!/usr/bin/env node
/**
 * Octopoid CLI
 * Main entry point for command-line interface
 */

import { Command } from 'commander'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

// Get package.json path
const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const packageJson = JSON.parse(
  readFileSync(join(__dirname, '..', 'package.json'), 'utf-8')
)

// Import command implementations
import { initCommand } from './commands/init'
import { startCommand } from './commands/start'
import { statusCommand } from './commands/status'
import { enqueueCommand } from './commands/enqueue'
import { listCommand } from './commands/list'
import { registerDraftCommands } from './commands/draft'
import { registerProjectCommands } from './commands/project'
import { approveCommand, rejectCommand, showTaskCommand } from './commands/task'

const program = new Command()

program
  .name('octopoid')
  .description('Distributed AI orchestrator for software development')
  .version(packageJson.version)

// Init command
program
  .command('init')
  .description('Initialize Octopoid in current directory')
  .option('--server <url>', 'Server URL for remote mode')
  .option('--cluster <name>', 'Cluster name (e.g., prod, dev)')
  .option('--machine-id <id>', 'Machine identifier')
  .option('--local', 'Use local mode (no server)', false)
  .action(initCommand)

// Start command
program
  .command('start')
  .description('Start orchestrator')
  .option('--daemon', 'Run as daemon', false)
  .option('--once', 'Run single tick (for testing)', false)
  .option('--debug', 'Enable debug logging', false)
  .option('--tick-interval <ms>', 'Tick interval in milliseconds', '60000')
  .action(async (options) => {
    await startCommand({
      daemon: options.daemon,
      once: options.once,
      debug: options.debug,
      tickInterval: parseInt(options.tickInterval),
    })
  })

// Stop command
program
  .command('stop')
  .description('Stop orchestrator')
  .action(async () => {
    console.log('🛑 Stopping orchestrator...')
    console.log('❌ Stop command not yet implemented')
    process.exit(1)
  })

// Status command
program
  .command('status')
  .description('Show orchestrator status')
  .action(statusCommand)

// Enqueue command (create task)
program
  .command('enqueue <description>')
  .description('Create a new task')
  .option('--role <role>', 'Task role (implement, test, review, etc.)')
  .option('--priority <priority>', 'Priority (P0, P1, P2, P3)', 'P2')
  .option('--project <id>', 'Project ID')
  .option('--complexity <complexity>', 'Complexity (XS, S, M, L, XL)')
  .action(enqueueCommand)

// List command
program
  .command('list')
  .description('List tasks')
  .option('--queue <queue>', 'Filter by queue')
  .option('--priority <priority>', 'Filter by priority')
  .option('--role <role>', 'Filter by role')
  .option('--limit <limit>', 'Maximum number of tasks to list', '100')
  .action((options) => {
    listCommand({
      ...options,
      limit: parseInt(options.limit),
    })
  })

// Validate command
program
  .command('validate')
  .description('Validate configuration')
  .action(async () => {
    console.log('✅ Validating configuration...')
    console.log('❌ Validate command not yet implemented')
    process.exit(1)
  })

// Draft commands
registerDraftCommands(program)

// Project commands
registerProjectCommands(program)

// Task management commands
program
  .command('approve <task-id>')
  .description('Approve a task completion (move from provisional to done)')
  .option('--by <name>', 'Who is approving (defaults to config user or "manual-review")')
  .action(approveCommand)

program
  .command('reject <task-id> <reason>')
  .description('Reject a task completion (move back to incoming for retry)')
  .option('--by <name>', 'Who is rejecting (defaults to config user or "manual-review")')
  .action(rejectCommand)

program
  .command('show <task-id>')
  .description('Show detailed task information')
  .action(showTaskCommand)

// Parse arguments
program.parse()
