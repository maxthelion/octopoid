/**
 * Octopoid Server - Cloudflare Workers API
 * Main entry point for the Hono application
 */

import { Hono } from 'hono'
import { cors } from 'hono/cors'
import type { D1Database } from '@cloudflare/workers-types'
import type { HealthCheckResponse } from '@octopoid/shared'

import { getConfig } from './config'
import { healthCheck as dbHealthCheck } from './database'

// Environment bindings
export interface Env {
  DB: D1Database
  ANTHROPIC_API_KEY?: string  // Optional, for future server-side features
  API_SECRET_KEY?: string     // For admin endpoints
}

// Create Hono app with typed environment
const app = new Hono<{ Bindings: Env }>()

// Middleware
app.use('/*', cors())

// Health check endpoint
app.get('/api/health', async (c) => {
  const db = c.env.DB
  const config = getConfig()

  const dbConnected = await dbHealthCheck(db)

  const response: HealthCheckResponse = {
    status: dbConnected ? 'healthy' : 'unhealthy',
    version: config.version,
    timestamp: new Date().toISOString(),
    database: dbConnected ? 'connected' : 'disconnected',
  }

  const statusCode = dbConnected ? 200 : 503
  return c.json(response, statusCode)
})

// Root endpoint
app.get('/', (c) => {
  return c.json({
    name: 'Octopoid Server',
    version: getConfig().version,
    endpoints: [
      'GET  /api/health',
      'POST /api/v1/orchestrators/register',
      'POST /api/v1/orchestrators/:id/heartbeat',
      'GET  /api/v1/orchestrators',
      'GET  /api/v1/tasks',
      'POST /api/v1/tasks',
      'GET  /api/v1/tasks/:id',
      'POST /api/v1/tasks/claim',
      'POST /api/v1/tasks/:id/submit',
      'POST /api/v1/tasks/:id/accept',
      'POST /api/v1/tasks/:id/reject',
      'GET  /api/v1/drafts',
      'POST /api/v1/drafts',
      'GET  /api/v1/drafts/:id',
      'PATCH /api/v1/drafts/:id',
      'DELETE /api/v1/drafts/:id',
      'GET  /api/v1/projects',
      'POST /api/v1/projects',
      'GET  /api/v1/projects/:id',
      'GET  /api/v1/projects/:id/tasks',
      'PATCH /api/v1/projects/:id',
      'DELETE /api/v1/projects/:id',
    ],
  })
})

// Mount route modules
import { tasksRoute } from './routes/tasks'
import { orchestratorsRoute } from './routes/orchestrators'
import { draftsRoute } from './routes/drafts'
import { projectsRoute } from './routes/projects'
app.route('/api/v1/tasks', tasksRoute)
app.route('/api/v1/orchestrators', orchestratorsRoute)
app.route('/api/v1/drafts', draftsRoute)
app.route('/api/v1/projects', projectsRoute)

// 404 handler
app.notFound((c) => {
  return c.json(
    {
      error: 'Not Found',
      message: `Endpoint ${c.req.url} not found`,
      timestamp: new Date().toISOString(),
    },
    404
  )
})

// Error handler
app.onError((err, c) => {
  console.error('Server error:', err)
  return c.json(
    {
      error: 'Internal Server Error',
      message: err.message,
      timestamp: new Date().toISOString(),
    },
    500
  )
})

// Export for Cloudflare Workers
export default app

// Scheduled handler for cron jobs (lease expiration, etc.)
export async function scheduled(
  _event: ScheduledEvent,
  env: Env,
  _ctx: ExecutionContext
): Promise<void> {
  console.log('Scheduled job triggered at:', new Date().toISOString())

  // Import and run scheduled jobs
  const { runLeaseMonitor } = await import('./scheduled/lease-monitor')
  await runLeaseMonitor(env.DB)
}
