/**
 * Status command implementation
 * Shows orchestrator and task status
 */

import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import chalk from 'chalk'
import { loadConfig, isRemoteMode, getRuntimeDir } from '../config'
import { healthCheck, listTasks, getSyncStatus, isOfflineMode } from '../db-interface'
import { OctopoidAPIClient } from '../api-client'

export async function statusCommand(): Promise<void> {
  try {
    const config = loadConfig()

    console.log(chalk.bold('📊 Octopoid Status'))
    console.log('')

    // Configuration
    console.log(chalk.bold('Configuration:'))
    console.log(`  Mode: ${config.mode}`)
    if (config.server) {
      console.log(`  Server: ${config.server.url}`)
      console.log(`  Cluster: ${config.server.cluster}`)
      console.log(`  Machine ID: ${config.server.machine_id}`)
    }
    console.log('')

    // Server connection (remote mode)
    if (isRemoteMode() && config.server?.url) {
      console.log(chalk.bold('Server Connection:'))

      // Check offline mode
      const offline = isOfflineMode()
      const syncStatus = getSyncStatus()

      try {
        const healthy = await healthCheck()
        if (healthy) {
          console.log(chalk.green('  ✓ Connected'))
        } else {
          console.log(chalk.yellow('  ⚠️  Server unhealthy'))
        }

        // Get orchestrator info
        const client = new OctopoidAPIClient(config.server.url, {
          apiKey: config.server.api_key,
        })
        const orchestratorId = `${config.server.cluster}-${config.server.machine_id}`
        try {
          const orchestrator = await client.getOrchestrator(orchestratorId)
          console.log(`  Status: ${orchestrator.status}`)
          console.log(`  Last heartbeat: ${new Date(orchestrator.last_heartbeat).toLocaleString()}`)
        } catch {
          console.log(chalk.yellow('  ⚠️  Not registered (run "octopoid start" to register)'))
        }
      } catch (error) {
        console.log(chalk.red('  ✗ Cannot connect to server'))
        if (error instanceof Error) {
          console.log(chalk.gray(`    ${error.message}`))
        }
      }

      // Show offline mode status
      if (offline || syncStatus.pending || syncStatus.failed) {
        console.log('')
        console.log(chalk.bold('Offline Mode:'))
        if (offline) {
          console.log(chalk.yellow('  ⚠️  Working offline'))
        } else {
          console.log(chalk.green('  ✓ Online'))
        }

        if (syncStatus.pending) {
          console.log(chalk.yellow(`  Pending sync: ${syncStatus.pending} operations`))
        }

        if (syncStatus.failed) {
          console.log(chalk.red(`  Failed sync: ${syncStatus.failed} operations`))
        }
      }

      console.log('')
    }

    // Orchestrator process
    console.log(chalk.bold('Orchestrator:'))
    try {
      const runtimeDir = getRuntimeDir()
      const pidFile = join(runtimeDir, 'orchestrator.pid')

      if (existsSync(pidFile)) {
        const pid = parseInt(readFileSync(pidFile, 'utf-8').trim())
        // Check if process is running
        try {
          process.kill(pid, 0) // Signal 0 checks if process exists
          console.log(chalk.green(`  ✓ Running (PID: ${pid})`))
        } catch {
          console.log(chalk.yellow(`  ⚠️  PID file exists but process not running`))
          console.log(chalk.gray(`    Stale PID: ${pid}`))
        }
      } else {
        console.log(chalk.gray('  ✗ Not running'))
      }
    } catch {
      console.log(chalk.gray('  ✗ Not running'))
    }
    console.log('')

    // Tasks
    console.log(chalk.bold('Tasks:'))
    try {
      const [incomingTasks, claimedTasks, provisionalTasks, doneTasks] =
        await Promise.all([
          listTasks({ queue: 'incoming', limit: 1000 }),
          listTasks({ queue: 'claimed', limit: 1000 }),
          listTasks({ queue: 'provisional', limit: 1000 }),
          listTasks({ queue: 'done', limit: 1000 }),
        ])

      console.log(`  Incoming: ${incomingTasks.length}`)
      console.log(`  Claimed: ${claimedTasks.length}`)
      console.log(`  Provisional: ${provisionalTasks.length}`)
      console.log(`  Done: ${doneTasks.length}`)

      if (incomingTasks.length > 0) {
        console.log('')
        console.log(chalk.bold('Recent Incoming Tasks:'))
        incomingTasks.slice(0, 5).forEach((task) => {
          console.log(`  • ${task.id} (${task.priority}) - ${task.role || 'no role'}`)
        })
      }
    } catch (error) {
      console.log(chalk.red('  ✗ Cannot fetch task list'))
      if (error instanceof Error) {
        console.log(chalk.gray(`    ${error.message}`))
      }
    }
  } catch (error) {
    console.error(chalk.red('❌ Error:'), error instanceof Error ? error.message : error)
    process.exit(1)
  }
}
