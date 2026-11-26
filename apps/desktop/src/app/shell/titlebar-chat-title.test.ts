import { describe, expect, it } from 'vitest'

import { chatTitleFor } from './titlebar-chat-title'

describe('chatTitleFor', () => {
  const sessions = [
    { id: 'a', title: ' Quote for Tremblay ', preview: 'hi' },
    { id: 'b', title: '', preview: 'Plan my week' },
    { id: 'c', title: null, preview: null }
  ]

  it('prefers the title, trimmed', () => expect(chatTitleFor(sessions, 'a')).toBe('Quote for Tremblay'))
  it('falls back to the preview', () => expect(chatTitleFor(sessions, 'b')).toBe('Plan my week'))
  it('is null when there is nothing to name', () => {
    expect(chatTitleFor(sessions, 'c')).toBeNull()
    expect(chatTitleFor(sessions, null)).toBeNull()
    expect(chatTitleFor(sessions, 'zzz')).toBeNull()
  })
})
