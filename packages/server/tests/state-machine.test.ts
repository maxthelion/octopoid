/**
 * State machine tests
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { executeTransition, VALID_TRANSITIONS } from '../src/state-machine'

describe('State Machine', () => {
  describe('Transition Validation', () => {
    it('should have claim transition', () => {
      expect(VALID_TRANSITIONS.has('claim')).toBe(true)
      const claimTransition = VALID_TRANSITIONS.get('claim')!
      expect(claimTransition.fromState).toBe('incoming')
      expect(claimTransition.toState).toBe('claimed')
    })

    it('should have submit transition', () => {
      expect(VALID_TRANSITIONS.has('submit')).toBe(true)
      const submitTransition = VALID_TRANSITIONS.get('submit')!
      expect(submitTransition.fromState).toBe('claimed')
      expect(submitTransition.toState).toBe('provisional')
    })

    it('should have accept transition', () => {
      expect(VALID_TRANSITIONS.has('accept')).toBe(true)
      const acceptTransition = VALID_TRANSITIONS.get('accept')!
      expect(acceptTransition.fromState).toBe('provisional')
      expect(acceptTransition.toState).toBe('done')
    })

    it('should have reject transition', () => {
      expect(VALID_TRANSITIONS.has('reject')).toBe(true)
      const rejectTransition = VALID_TRANSITIONS.get('reject')!
      expect(rejectTransition.fromState).toBe('provisional')
      expect(rejectTransition.toState).toBe('incoming')
    })
  })

  describe('Guard Functions', () => {
    it('should validate dependencies_resolved guard', () => {
      const claimTransition = VALID_TRANSITIONS.get('claim')!
      expect(claimTransition.guards).toContain('dependencies_resolved')
    })

    it('should validate role_matches guard for claim', () => {
      const claimTransition = VALID_TRANSITIONS.get('claim')!
      expect(claimTransition.guards).toContain('role_matches')
    })
  })

  describe('Side Effects', () => {
    it('should record history on all transitions', () => {
      for (const [name, transition] of VALID_TRANSITIONS) {
        expect(transition.sideEffects).toContain('record_history')
      }
    })

    it('should set claimed_by on claim transition', () => {
      const claimTransition = VALID_TRANSITIONS.get('claim')!
      expect(claimTransition.sideEffects).toContain('set_claimed_by')
    })

    it('should unblock dependents on accept transition', () => {
      const acceptTransition = VALID_TRANSITIONS.get('accept')!
      expect(acceptTransition.sideEffects).toContain('unblock_dependents')
    })
  })
})
