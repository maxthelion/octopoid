/**
 * State machine types for Octopoid v2.0
 * Defines valid state transitions and guards
 */

import type { TaskQueue } from './task.js'

export interface StateTransitionGuard {
  type: 'dependency_resolved' | 'role_matches' | 'lease_valid' | 'version_matches'
  params?: Record<string, unknown>
}

export interface StateTransitionSideEffect {
  type: 'record_history' | 'unblock_dependents' | 'notify_webhook' | 'update_lease'
  params?: Record<string, unknown>
}

export interface StateTransition {
  from: TaskQueue
  to: TaskQueue
  action: string  // e.g., 'claim', 'submit', 'accept', 'reject'
  guards: StateTransitionGuard[]
  side_effects: StateTransitionSideEffect[]
}

export interface StateTransitionRequest {
  task_id: string
  from: TaskQueue
  to: TaskQueue
  action: string
  params?: Record<string, unknown>
  version?: number  // For optimistic locking
}

export interface StateTransitionResponse {
  success: boolean
  new_state: TaskQueue
  version: number
  errors?: string[]
}

// Valid state transitions map
export const VALID_TRANSITIONS: Record<string, StateTransition> = {
  claim: {
    from: 'incoming',
    to: 'claimed',
    action: 'claim',
    guards: [
      { type: 'dependency_resolved' },
      { type: 'role_matches' },
    ],
    side_effects: [
      { type: 'record_history', params: { event: 'claimed' } },
      { type: 'update_lease' },
    ],
  },
  submit: {
    from: 'claimed',
    to: 'provisional',
    action: 'submit',
    guards: [
      { type: 'lease_valid' },
      { type: 'version_matches' },
    ],
    side_effects: [
      { type: 'record_history', params: { event: 'submitted' } },
    ],
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
    side_effects: [
      { type: 'record_history', params: { event: 'rejected' } },
    ],
  },
  requeue: {
    from: 'claimed',
    to: 'incoming',
    action: 'requeue',
    guards: [],
    side_effects: [
      { type: 'record_history', params: { event: 'requeued' } },
    ],
  },
}
