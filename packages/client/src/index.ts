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

// Re-export shared types for convenience
export type * from '@octopoid/shared'
