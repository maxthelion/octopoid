/**
 * Init command implementation
 * Creates .octopoid directory and configuration
 */

import { mkdirSync, writeFileSync, existsSync, readFileSync, copyFileSync } from 'node:fs'
import { join } from 'node:path'
import { hostname } from 'node:os'
import YAML from 'yaml'
import type { OctopoidConfig } from '../config'

export interface InitOptions {
  server?: string
  cluster?: string
  machineId?: string
  local?: boolean
}

export async function initCommand(options: InitOptions): Promise<void> {
  const cwd = process.cwd()
  const octopoidDir = join(cwd, '.octopoid')

  // Check if already initialized
  if (existsSync(octopoidDir)) {
    console.log('⚠️  .octopoid directory already exists')
    const configPath = join(octopoidDir, 'config.yaml')
    if (existsSync(configPath)) {
      console.log('Configuration found at:', configPath)
      console.log('To reconfigure, edit the file manually or delete .octopoid and run init again')
      return
    }
  }

  // Create directories
  console.log('📁 Creating .octopoid directory structure...')
  mkdirSync(octopoidDir, { recursive: true })
  mkdirSync(join(octopoidDir, 'runtime'), { recursive: true })
  mkdirSync(join(octopoidDir, 'logs'), { recursive: true })
  mkdirSync(join(octopoidDir, 'logs', 'agents'), { recursive: true })
  mkdirSync(join(octopoidDir, 'worktrees'), { recursive: true })

  // Determine mode
  const mode = options.local ? 'local' : 'remote'

  // Create config
  const config: OctopoidConfig = {
    mode,
    repo: {
      path: cwd,
      main_branch: 'main',
    },
    agents: {
      max_concurrent: 3,
    },
  }

  if (mode === 'remote') {
    if (!options.server) {
      console.error('❌ Error: --server is required for remote mode')
      process.exit(1)
    }

    config.server = {
      enabled: true,
      url: options.server,
      cluster: options.cluster || 'default',
      machine_id: options.machineId || hostname(),
    }

    console.log('⚙️  Remote mode configuration:')
    console.log(`   Server: ${config.server.url}`)
    console.log(`   Cluster: ${config.server.cluster}`)
    console.log(`   Machine ID: ${config.server.machine_id}`)
  } else {
    config.database = {
      path: join(octopoidDir, 'state.db'),
    }

    console.log('⚙️  Local mode configuration (no server)')
  }

  // Write config
  const configPath = join(octopoidDir, 'config.yaml')
  writeFileSync(configPath, YAML.stringify(config), 'utf-8')
  console.log('✅ Created:', configPath)

  // Copy agents template
  const agentsPath = join(octopoidDir, 'agents.yaml')
  const templatePath = join(__dirname, '..', '..', 'templates', 'agents.yaml')
  if (existsSync(templatePath)) {
    copyFileSync(templatePath, agentsPath)
    console.log('✅ Created:', agentsPath)
  }

  // Create .gitignore
  const gitignorePath = join(octopoidDir, '.gitignore')
  writeFileSync(
    gitignorePath,
    `# Octopoid runtime files
runtime/
logs/
worktrees/
*.db
*.db-shm
*.db-wal
cache.db
`,
    'utf-8'
  )
  console.log('✅ Created:', gitignorePath)

  console.log('')
  console.log('🎉 Octopoid initialized successfully!')
  console.log('')
  console.log('Next steps:')
  console.log('  1. Review configuration: .octopoid/config.yaml')
  console.log('  2. Configure agents: .octopoid/agents.yaml')
  if (mode === 'remote') {
    console.log('  3. Start orchestrator: octopoid start')
  } else {
    console.log('  3. Initialize database: octopoid db init')
    console.log('  4. Start orchestrator: octopoid start')
  }
}
