/**
 * Agent roles export
 */

import { BaseAgent as BaseAgentClass, type AgentConfig } from './base-agent'
import { Implementer as ImplementerClass } from './implementer'
import { Breakdown as BreakdownClass } from './breakdown'
import { Gatekeeper as GatekeeperClass } from './gatekeeper'

export { BaseAgentClass as BaseAgent, type AgentConfig }
export { ImplementerClass as Implementer }
export { BreakdownClass as Breakdown }
export { GatekeeperClass as Gatekeeper }

/**
 * Constructor type for agent classes
 */
export type AgentConstructor = new (config: AgentConfig) => BaseAgentClass

/**
 * Get agent class by role name
 */
export function getAgentByRole(role: string): AgentConstructor | null {
  switch (role.toLowerCase()) {
    case 'implement':
    case 'implementer':
      return ImplementerClass

    case 'breakdown':
      return BreakdownClass

    case 'review':
    case 'gatekeeper':
      return GatekeeperClass

    default:
      return null
  }
}
