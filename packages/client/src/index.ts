/**
 * Octopoid Client - Main entry point
 * Exports all public APIs for use as library
 */

// Configuration
export { loadConfig, isRemoteMode, getServerUrl, findOctopoidDir } from './config'

// API Client
export { OctopoidAPIClient } from './api-client'

// Database Interface
export * from './db-interface'

// Queue Utilities
export * from './queue-utils'

// Git Utilities
export * from './git-utils'

// Re-export shared types for convenience
export type * from '@octopoid/shared'
