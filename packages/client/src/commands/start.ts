/**
 * Start command - runs the orchestrator scheduler
 */

import { findOctopoidDir, loadConfig } from '../config'
import { runSchedulerDaemon } from '../scheduler'

export interface StartOptions {
  debug?: boolean
  once?: boolean
  daemon?: boolean
  tickInterval?: number
}

/**
 * Start the orchestrator scheduler
 */
export async function startCommand(options: StartOptions = {}): Promise<void> {
  // Verify we're in an Octopoid project
  const octopoidDir = findOctopoidDir()
  if (!octopoidDir) {
    console.error('Error: Not in an Octopoid project directory')
    console.error('Run "octopoid init" to set up Octopoid first')
    process.exit(1)
  }

  // Load and verify config
  try {
    const config = loadConfig()

    // Show startup banner
    console.log('╭──────────────────────────────────╮')
    console.log('│   Octopoid Orchestrator v2.0     │')
    console.log('╰──────────────────────────────────╯')
    console.log()
    console.log(`Mode: ${config.mode}`)

    if (config.mode === 'remote' && config.server) {
      console.log(`Server: ${config.server.url}`)
      console.log(`Cluster: ${config.server.cluster}`)
    } else {
      console.log(`Database: ${config.database?.path || 'default'}`)
    }

    console.log()

    if (options.once) {
      console.log('Running scheduler once...')
    } else if (options.daemon) {
      console.log('Starting scheduler daemon...')
      console.log('Press Ctrl+C to stop')
    } else {
      console.log('Starting scheduler...')
      console.log('Press Ctrl+C to stop')
    }
    console.log()

  } catch (error) {
    console.error('Error loading configuration:', error)
    process.exit(1)
  }

  // Run scheduler
  try {
    await runSchedulerDaemon({
      debug: options.debug,
      once: options.once,
      tickInterval: options.tickInterval,
    })
  } catch (error) {
    console.error('Scheduler error:', error)
    process.exit(1)
  }
}
