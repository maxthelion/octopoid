/**
 * Main scheduler - runs on tick intervals to evaluate and spawn agents
 *
 * Ported from orchestrator/scheduler.py
 * This is the core orchestration loop that:
 * - Loads agent configurations
 * - Checks which agents should run (based on interval, state, backpressure)
 * - Spawns agents (instantiates agent role classes)
 * - Tracks agent state
 * - Handles orchestrator registration and heartbeat (remote mode)
 */

import { hostname } from 'node:os'
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import {
  loadConfig,
  isRemoteMode,
  getAgentsConfig,
  getRuntimeDir,
  getLogsDir,
  type AgentConfigItem,
} from './config'
import { OctopoidAPIClient } from './api-client'
import { getAgentByRole } from './roles'

// Agent state tracking
interface AgentState {
  running: boolean
  pid?: number
  lastFinished?: string // ISO timestamp
  lastStarted?: string // ISO timestamp
  currentTask?: string
  exitCode?: number
  extra: Record<string, unknown>
}

// Global debug flag
let DEBUG = false
let _logFile: string | null = null

/**
 * Set up debug logging for the scheduler
 */
function setupSchedulerDebug(): void {
  const logsDir = getLogsDir()
  mkdirSync(logsDir, { recursive: true })

  const dateStr = new Date().toISOString().split('T')[0]
  _logFile = join(logsDir, `scheduler-${dateStr}.log`)
}

/**
 * Write a debug message to the scheduler log
 */
function debugLog(message: string): void {
  if (!DEBUG || !_logFile) {
    return
  }

  const timestamp = new Date().toISOString()
  const logLine = `[${timestamp}] [SCHEDULER] ${message}\n`

  try {
    writeFileSync(_logFile, logLine, { flag: 'a' })
  } catch (error) {
    // Silently fail
  }
}

/**
 * Load agent state from JSON file
 */
function loadState(statePath: string): AgentState {
  if (!existsSync(statePath)) {
    return {
      running: false,
      extra: {},
    }
  }

  try {
    const content = readFileSync(statePath, 'utf-8')
    return JSON.parse(content) as AgentState
  } catch (error) {
    debugLog(`Failed to parse state file ${statePath}: ${error}`)
    return {
      running: false,
      extra: {},
    }
  }
}

/**
 * Save agent state to JSON file
 */
function saveState(state: AgentState, statePath: string): void {
  try {
    const dir = join(statePath, '..')
    mkdirSync(dir, { recursive: true })
    writeFileSync(statePath, JSON.stringify(state, null, 2))
  } catch (error) {
    debugLog(`Failed to save state file ${statePath}: ${error}`)
  }
}

/**
 * Mark agent as started
 */
function markStarted(state: AgentState, taskId?: string): AgentState {
  return {
    ...state,
    running: true,
    lastStarted: new Date().toISOString(),
    currentTask: taskId,
  }
}

/**
 * Mark agent as finished
 */
function markFinished(state: AgentState, exitCode: number): AgentState {
  return {
    ...state,
    running: false,
    lastFinished: new Date().toISOString(),
    exitCode,
    currentTask: undefined,
  }
}

/**
 * Check if agent is overdue (should run based on interval)
 */
function isOverdue(state: AgentState, intervalSeconds: number): boolean {
  if (!state.lastFinished) {
    return true // Never run before
  }

  try {
    const lastFinished = new Date(state.lastFinished).getTime()
    const now = Date.now()
    const elapsed = (now - lastFinished) / 1000

    return elapsed >= intervalSeconds
  } catch (error) {
    return true // Parse error, assume overdue
  }
}

/**
 * Get path to agent's state file
 */
function getAgentStatePath(agentName: string): string {
  const runtimeDir = getRuntimeDir()
  return join(runtimeDir, 'agents', agentName, 'state.json')
}

/**
 * Register orchestrator with server (remote mode only)
 */
async function registerOrchestrator(): Promise<string> {
  if (!isRemoteMode()) {
    return 'local'
  }

  const config = loadConfig()
  if (!config.server?.url || !config.server?.cluster) {
    throw new Error('Server URL and cluster required for remote mode')
  }

  const client = new OctopoidAPIClient(config.server.url)

  const machineId = config.server.machine_id || hostname()

  // Try to register
  const response = await client.request<{ orchestrator_id: string }>(
    'POST',
    '/api/v1/orchestrators/register',
    {
      cluster: config.server.cluster,
      machine_id: machineId,
      repo_url: process.cwd(), // TODO: Get actual git repo URL
      capabilities: {
        roles: ['implement', 'breakdown', 'review'],
      },
      version: '2.0.0',
    }
  )

  if (!response) {
    throw new Error('Failed to register orchestrator with server')
  }

  const orchestratorId = response.orchestrator_id

  // Persist ID for future runs
  const runtimeDir = getRuntimeDir()
  const idPath = join(runtimeDir, 'orchestrator_id.txt')
  mkdirSync(runtimeDir, { recursive: true })
  writeFileSync(idPath, orchestratorId)

  debugLog(`Registered as orchestrator ${orchestratorId}`)
  return orchestratorId
}

/**
 * Get orchestrator ID (from file or register)
 */
async function getOrchestratorId(): Promise<string> {
  if (!isRemoteMode()) {
    return 'local'
  }

  const runtimeDir = getRuntimeDir()
  const idPath = join(runtimeDir, 'orchestrator_id.txt')

  if (existsSync(idPath)) {
    try {
      return readFileSync(idPath, 'utf-8').trim()
    } catch (error) {
      debugLog(`Failed to read orchestrator ID: ${error}`)
    }
  }

  // Register for the first time
  return await registerOrchestrator()
}

/**
 * Send heartbeat to server (remote mode only)
 */
async function sendHeartbeat(orchestratorId: string): Promise<void> {
  if (!isRemoteMode()) {
    return
  }

  const config = loadConfig()
  if (!config.server?.url) {
    return
  }

  const client = new OctopoidAPIClient(config.server.url)

  try {
    await client.request(
      'POST',
      `/api/v1/orchestrators/${orchestratorId}/heartbeat`,
      {
        timestamp: new Date().toISOString(),
      }
    )
  } catch (error) {
    debugLog(`Failed to send heartbeat: ${error}`)
  }
}

/**
 * Run an agent (instantiate and call run() method)
 */
async function runAgent(
  agentName: string,
  agentConfig: AgentConfigItem
): Promise<number> {
  const { role, model, max_turns } = agentConfig

  debugLog(`Running agent ${agentName} (role: ${role})`)

  try {
    // Get agent class constructor
    const AgentClass = getAgentByRole(role)
    if (!AgentClass) {
      debugLog(`Unknown role: ${role}`)
      return 1
    }

    // Instantiate agent
    const agent = new AgentClass({
      name: agentName,
      role,
      model: model || 'claude-sonnet-4-20250514',
      maxTurns: max_turns || 50,
    })

    // Run agent (this is async)
    await agent.run()

    debugLog(`Agent ${agentName} completed successfully`)
    return 0
  } catch (error) {
    debugLog(`Agent ${agentName} failed: ${error}`)
    console.error(`Agent ${agentName} error:`, error)
    return 1
  }
}

/**
 * Check and update finished agents
 *
 * This is different from Python version - we track agent runs
 * as async operations rather than subprocesses
 */
function checkAndUpdateFinishedAgents(): void {
  // In TypeScript version, agents run in the same process as async functions
  // State is updated synchronously when agent.run() completes
  // This function is kept for compatibility but does less work

  debugLog('Checking for finished agents')
  // TODO: If we track running agents in a map, check their promises here
}

/**
 * Main scheduler loop - evaluate and spawn agents
 */
async function runScheduler(_orchestratorId: string): Promise<void> {
  console.log(`[${new Date().toISOString()}] Scheduler starting`)
  debugLog('Scheduler tick starting')

  // Check for finished agents
  checkAndUpdateFinishedAgents()

  // Load agent configuration
  let agents: AgentConfigItem[]
  try {
    agents = getAgentsConfig()
    debugLog(`Loaded ${agents.length} agents from config`)
  } catch (error) {
    console.error('Error loading agents config:', error)
    debugLog(`Failed to load agents config: ${error}`)
    return
  }

  if (agents.length === 0) {
    console.log('No agents configured in agents.yaml')
    debugLog('No agents configured')
    return
  }

  // Evaluate each agent
  for (const agentConfig of agents) {
    const { name: agentName, role, interval_seconds, paused } = agentConfig

    if (!agentName || !role) {
      console.log(`Skipping invalid agent config: ${JSON.stringify(agentConfig)}`)
      debugLog(`Invalid agent config: ${JSON.stringify(agentConfig)}`)
      continue
    }

    if (paused) {
      console.log(`Agent ${agentName} is paused, skipping`)
      debugLog(`Agent ${agentName} is paused`)
      continue
    }

    const interval = interval_seconds || 300
    debugLog(`Evaluating agent ${agentName}: role=${role}, interval=${interval}s`)

    // Load agent state
    const statePath = getAgentStatePath(agentName)
    const state = loadState(statePath)
    debugLog(
      `Agent ${agentName} state: running=${state.running}, ` +
      `lastFinished=${state.lastFinished}`
    )

    // Check if still running (in our case, we track this differently)
    if (state.running) {
      console.log(`Agent ${agentName} is still running`)
      debugLog(`Agent ${agentName} still running`)
      continue
    }

    // Check if overdue
    if (!isOverdue(state, interval)) {
      console.log(`Agent ${agentName} is not due yet`)
      debugLog(`Agent ${agentName} not due yet`)
      continue
    }

    console.log(`[${new Date().toISOString()}] Starting agent ${agentName} (role: ${role})`)
    debugLog(`Starting agent ${agentName} (role: ${role})`)

    // Mark as started
    const newState = markStarted(state)
    saveState(newState, statePath)

    // Run agent (async, but we don't await - fire and forget)
    runAgent(agentName, agentConfig)
      .then((exitCode) => {
        debugLog(`Agent ${agentName} finished with exit code ${exitCode}`)
        const finishedState = markFinished(newState, exitCode)
        saveState(finishedState, statePath)
        console.log(`[${new Date().toISOString()}] Agent ${agentName} finished (exit code: ${exitCode})`)
      })
      .catch((error) => {
        debugLog(`Agent ${agentName} crashed: ${error}`)
        const crashedState = markFinished(newState, 1)
        saveState(crashedState, statePath)
        console.error(`Agent ${agentName} crashed:`, error)
      })
  }

  console.log(`[${new Date().toISOString()}] Scheduler tick complete`)
  debugLog('Scheduler tick complete')
}

/**
 * Main scheduler daemon - runs continuously
 */
export async function runSchedulerDaemon(options: {
  debug?: boolean
  once?: boolean
  tickInterval?: number
}): Promise<void> {
  DEBUG = options.debug || false
  const tickInterval = options.tickInterval || 60000 // 60 seconds default

  if (DEBUG) {
    setupSchedulerDebug()
    debugLog('Scheduler starting with debug mode enabled')
    console.log('Debug mode enabled - logs in .octopoid/logs/')
  }

  // Get or register orchestrator ID
  const orchestratorId = await getOrchestratorId()
  console.log(`Orchestrator ID: ${orchestratorId}`)

  // Run once or loop
  if (options.once) {
    await runScheduler(orchestratorId)
    return
  }

  // Main loop
  let running = true

  // Handle graceful shutdown
  const shutdown = async () => {
    console.log('\nShutting down scheduler...')
    running = false
  }

  process.on('SIGINT', shutdown)
  process.on('SIGTERM', shutdown)

  while (running) {
    try {
      // Run scheduler tick
      await runScheduler(orchestratorId)

      // Send heartbeat (remote mode)
      if (isRemoteMode()) {
        await sendHeartbeat(orchestratorId)
      }

      // Wait for next tick
      if (running) {
        await delay(tickInterval)
      }
    } catch (error) {
      console.error('Scheduler error:', error)
      debugLog(`Scheduler error: ${error}`)

      // Wait a bit before retrying
      await delay(5000)
    }
  }

  console.log('Scheduler stopped')
}
