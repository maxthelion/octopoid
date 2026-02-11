/**
 * Client configuration
 * Loads and manages .octopoid/config.yaml
 */

import { readFileSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import YAML from 'yaml'

export interface OctopoidConfig {
  // Mode: local (backward compat) or remote (client-server)
  mode: 'local' | 'remote'

  // Server configuration (remote mode)
  server?: {
    enabled: boolean
    url: string
    cluster: string
    machine_id?: string
    api_key?: string
  }

  // Local database (local mode)
  database?: {
    path: string
  }

  // Agent configuration
  agents?: {
    max_concurrent: number
    models?: Record<string, string>
  }

  // Repository
  repo?: {
    path: string
    main_branch: string
  }
}

let cachedConfig: OctopoidConfig | null = null

/**
 * Find .octopoid directory by walking up from current directory
 */
export function findOctopoidDir(startDir: string = process.cwd()): string | null {
  let dir = startDir

  while (dir !== '/') {
    const octopoidDir = join(dir, '.octopoid')
    if (existsSync(octopoidDir)) {
      return octopoidDir
    }
    dir = dirname(dir)
  }

  return null
}

/**
 * Get runtime directory for PIDs, locks, etc.
 */
export function getRuntimeDir(): string {
  const octopoidDir = findOctopoidDir()
  if (!octopoidDir) {
    throw new Error('Not in an Octopoid project directory')
  }
  return join(octopoidDir, 'runtime')
}

/**
 * Load configuration from .octopoid/config.yaml
 */
export function loadConfig(reload = false): OctopoidConfig {
  if (cachedConfig && !reload) {
    return cachedConfig
  }

  const octopoidDir = findOctopoidDir()
  if (!octopoidDir) {
    throw new Error(
      'No .octopoid directory found. Run "octopoid init" to set up Octopoid.'
    )
  }

  const configPath = join(octopoidDir, 'config.yaml')
  if (!existsSync(configPath)) {
    throw new Error(
      `Config file not found at ${configPath}. Run "octopoid init" to create it.`
    )
  }

  const configText = readFileSync(configPath, 'utf-8')
  const config = YAML.parse(configText) as OctopoidConfig

  // Set defaults
  if (!config.mode) {
    config.mode = 'local'
  }

  if (config.mode === 'local' && !config.database) {
    config.database = { path: join(octopoidDir, 'state.db') }
  }

  if (!config.agents) {
    config.agents = { max_concurrent: 3 }
  }

  if (!config.repo) {
    config.repo = {
      path: dirname(octopoidDir),
      main_branch: 'main',
    }
  }

  cachedConfig = config
  return config
}

/**
 * Check if running in remote mode
 */
export function isRemoteMode(): boolean {
  const config = loadConfig()
  return config.mode === 'remote' && config.server?.enabled === true
}

/**
 * Get server URL (throws if not in remote mode)
 */
export function getServerUrl(): string {
  const config = loadConfig()
  if (!isRemoteMode() || !config.server?.url) {
    throw new Error('Not configured for remote mode')
  }
  return config.server.url
}

/**
 * Get orchestrator ID (cluster-machine_id)
 */
export function getOrchestratorId(): string | null {
  const config = loadConfig()
  if (!config.server?.cluster || !config.server?.machine_id) {
    return null
  }
  return `${config.server.cluster}-${config.server.machine_id}`
}
