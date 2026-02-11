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
import { statusCommand } from './commands/status'
import { enqueueCommand } from './commands/enqueue'
import { listCommand } from './commands/list'

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
  .action(async (options) => {
    console.log('🚀 Starting orchestrator...')
    console.log('Options:', options)
    console.log('❌ Start command not yet implemented')
    process.exit(1)
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

// Parse arguments
program.parse()
