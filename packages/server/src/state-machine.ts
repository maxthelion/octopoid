/**
 * State machine for task transitions
 * Enforces valid state changes with guards and side effects
 */

import type { D1Database } from '@cloudflare/workers-types'
import type {
  TaskQueue,
  Task,
  StateTransition,
  StateTransitionGuard,
  StateTransitionSideEffect,
} from '@octopoid/shared'
import { queryOne, execute } from './database'

export interface TransitionContext {
  db: D1Database
  task: Task
  params: Record<string, unknown>
}

export interface TransitionResult {
  success: boolean
  newState?: TaskQueue
  newVersion?: number
  errors?: string[]
}

/**
 * Execute a guard check
 */
async function executeGuard(
  guard: StateTransitionGuard,
  context: TransitionContext
): Promise<{ passed: boolean; error?: string }> {
  const { db, task, params } = context

  switch (guard.type) {
    case 'dependency_resolved': {
      // Check if blocked_by task is done
      if (!task.blocked_by) {
        return { passed: true }
      }

      const blocker = await queryOne<Task>(
        db,
        'SELECT queue FROM tasks WHERE id = ?',
        task.blocked_by
      )

      if (!blocker) {
        return { passed: false, error: `Blocking task ${task.blocked_by} not found` }
      }

      if (blocker.queue !== 'done') {
        return {
          passed: false,
          error: `Task is blocked by ${task.blocked_by} (${blocker.queue})`,
        }
      }

      return { passed: true }
    }

    case 'role_matches': {
      const roleFilter = params.role_filter as string | string[] | undefined
      if (!roleFilter) {
        return { passed: true }
      }

      if (!task.role) {
        return { passed: false, error: 'Task has no role assigned' }
      }

      const roles = Array.isArray(roleFilter) ? roleFilter : [roleFilter]
      if (!roles.includes(task.role)) {
        return {
          passed: false,
          error: `Task role ${task.role} does not match filter ${roles.join(', ')}`,
        }
      }

      return { passed: true }
    }

    case 'lease_valid': {
      // Check if lease hasn't expired
      if (!task.lease_expires_at) {
        return { passed: false, error: 'No active lease' }
      }

      const now = new Date()
      const expiresAt = new Date(task.lease_expires_at)

      if (expiresAt < now) {
        return { passed: false, error: 'Lease has expired' }
      }

      return { passed: true }
    }

    case 'version_matches': {
      const expectedVersion = params.version as number | undefined
      if (expectedVersion !== undefined && task.version !== expectedVersion) {
        return {
          passed: false,
          error: `Version mismatch: expected ${expectedVersion}, got ${task.version}`,
        }
      }

      return { passed: true }
    }

    default:
      return { passed: false, error: `Unknown guard type: ${guard.type}` }
  }
}

/**
 * Execute a side effect
 */
async function executeSideEffect(
  effect: StateTransitionSideEffect,
  context: TransitionContext
): Promise<void> {
  const { db, task, params } = context

  switch (effect.type) {
    case 'record_history': {
      const event = effect.params?.event as string
      const agent = params.agent_name as string | undefined

      await execute(
        db,
        `INSERT INTO task_history (task_id, event, agent, timestamp)
         VALUES (?, ?, ?, datetime('now'))`,
        task.id,
        event,
        agent || null
      )
      break
    }

    case 'unblock_dependents': {
      // Find tasks blocked by this task and mark them as unblocked
      await execute(
        db,
        `UPDATE tasks
         SET blocked_by = NULL, updated_at = datetime('now')
         WHERE blocked_by = ?`,
        task.id
      )
      break
    }

    case 'update_lease': {
      const leaseDurationSeconds = (params.lease_duration_seconds as number) || 300
      const expiresAt = new Date(Date.now() + leaseDurationSeconds * 1000)

      await execute(
        db,
        `UPDATE tasks
         SET lease_expires_at = ?,
             orchestrator_id = ?,
             updated_at = datetime('now')
         WHERE id = ?`,
        expiresAt.toISOString(),
        params.orchestrator_id,
        task.id
      )
      break
    }

    case 'notify_webhook': {
      // Future: Send webhook notification
      console.log('Webhook notification (not implemented):', effect.params)
      break
    }

    default:
      console.warn('Unknown side effect type:', effect.type)
  }
}

/**
 * Execute a state transition
 */
export async function executeTransition(
  db: D1Database,
  taskId: string,
  transition: StateTransition,
  params: Record<string, unknown>
): Promise<TransitionResult> {
  // Get current task state
  const task = await queryOne<Task>(db, 'SELECT * FROM tasks WHERE id = ?', taskId)

  if (!task) {
    return {
      success: false,
      errors: [`Task ${taskId} not found`],
    }
  }

  // Verify current state matches expected "from" state
  if (task.queue !== transition.from) {
    return {
      success: false,
      errors: [
        `Invalid transition: task is in ${task.queue}, expected ${transition.from}`,
      ],
    }
  }

  const context: TransitionContext = { db, task, params }

  // Execute all guards
  const guardResults = await Promise.all(
    transition.guards.map((guard) => executeGuard(guard, context))
  )

  const failedGuards = guardResults.filter((r) => !r.passed)
  if (failedGuards.length > 0) {
    return {
      success: false,
      errors: failedGuards.map((r) => r.error || 'Guard failed'),
    }
  }

  // Update task state with optimistic locking
  const newVersion = task.version + 1
  const result = await execute(
    db,
    `UPDATE tasks
     SET queue = ?,
         version = ?,
         updated_at = datetime('now')
     WHERE id = ? AND version = ?`,
    transition.to,
    newVersion,
    taskId,
    task.version
  )

  if (result.meta.changes === 0) {
    return {
      success: false,
      errors: ['Optimistic lock failed - task was modified by another process'],
    }
  }

  // Execute side effects
  await Promise.all(
    transition.side_effects.map((effect) => executeSideEffect(effect, context))
  )

  return {
    success: true,
    newState: transition.to,
    newVersion,
  }
}

/**
 * Define all valid transitions
 */
export const TRANSITIONS: Record<string, StateTransition> = {
  claim: {
    from: 'incoming',
    to: 'claimed',
    action: 'claim',
    guards: [{ type: 'dependency_resolved' }, { type: 'role_matches' }],
    side_effects: [
      { type: 'record_history', params: { event: 'claimed' } },
      { type: 'update_lease' },
    ],
  },

  submit: {
    from: 'claimed',
    to: 'provisional',
    action: 'submit',
    guards: [{ type: 'lease_valid' }, { type: 'version_matches' }],
    side_effects: [{ type: 'record_history', params: { event: 'submitted' } }],
  },

  accept: {
    from: 'provisional',
    to: 'done',
    action: 'accept',
    guards: [],
    side_effects: [
      { type: 'record_history', params: { event: 'accepted' } },
      { type: 'unblock_dependents' },
    ],
  },

  reject: {
    from: 'provisional',
    to: 'incoming',
    action: 'reject',
    guards: [],
    side_effects: [{ type: 'record_history', params: { event: 'rejected' } }],
  },

  requeue: {
    from: 'claimed',
    to: 'incoming',
    action: 'requeue',
    guards: [],
    side_effects: [{ type: 'record_history', params: { event: 'requeued' } }],
  },

  block: {
    from: 'incoming',
    to: 'blocked',
    action: 'block',
    guards: [],
    side_effects: [{ type: 'record_history', params: { event: 'blocked' } }],
  },

  unblock: {
    from: 'blocked',
    to: 'incoming',
    action: 'unblock',
    guards: [{ type: 'dependency_resolved' }],
    side_effects: [{ type: 'record_history', params: { event: 'unblocked' } }],
  },
}
