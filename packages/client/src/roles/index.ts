/**
 * Agent roles export
 */

export { BaseAgent, type AgentConfig } from './base-agent'
export { Implementer } from './implementer'
export { Breakdown } from './breakdown'
export { Gatekeeper } from './gatekeeper'

/**
 * Get agent class by role name
 */
export function getAgentByRole(role: string): typeof BaseAgent | null {
  switch (role.toLowerCase()) {
    case 'implement':
    case 'implementer':
      return Implementer

    case 'breakdown':
      return Breakdown

    case 'review':
    case 'gatekeeper':
      return Gatekeeper

    default:
      return null
  }
}
