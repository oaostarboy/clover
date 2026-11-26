import { describe, expect, it } from 'vitest'

import { SESSION_STATUS_WORDS } from './session-status-dot'

describe('SESSION_STATUS_WORDS', () => {
  it('names the states that need a glance, in plain words', () => {
    expect(SESSION_STATUS_WORDS['needs-input']).toBe('Needs you')
    expect(SESSION_STATUS_WORDS.working).toBe('Working')
    expect(SESSION_STATUS_WORDS.unread).toBe('Done')
  })

  it('keeps idle, draft and background chats quiet', () => {
    expect(SESSION_STATUS_WORDS.idle).toBeUndefined()
    expect(SESSION_STATUS_WORDS.draft).toBeUndefined()
    expect(SESSION_STATUS_WORDS.background).toBeUndefined()
  })
})
