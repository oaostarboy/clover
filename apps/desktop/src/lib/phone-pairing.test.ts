import { describe, expect, it } from 'vitest'

import { formatCountdown, lastActiveLabel, newlyConnected, type PairedDevice } from './phone-pairing'

const dev = (over: Partial<PairedDevice> = {}): PairedDevice => ({
  connected: false,
  created_at: 1000,
  id: 'a1',
  last_seen_at: null,
  name: 'iPhone',
  ...over
})

describe('formatCountdown', () => {
  it('shows m:ss and never goes negative', () => {
    expect(formatCountdown(582)).toBe('9:42')
    expect(formatCountdown(600)).toBe('10:00')
    expect(formatCountdown(5.2)).toBe('0:06')
    expect(formatCountdown(-3)).toBe('0:00')
  })
})

describe('lastActiveLabel', () => {
  const now = 10_000_000

  it('describes never-used, fresh and older activity', () => {
    expect(lastActiveLabel(dev(), now)).toBe('Not connected yet')
    expect(lastActiveLabel(dev({ last_seen_at: now / 1000 - 10 }), now)).toBe('Active just now')
    expect(lastActiveLabel(dev({ last_seen_at: now / 1000 - 125 }), now)).toMatch(/^Last active .*2/)
  })
})

describe('newlyConnected', () => {
  it('fires only for the device minted for the shown code, once it has connected', () => {
    expect(newlyConnected([dev()], 'a1')).toBe(false)
    expect(newlyConnected([dev({ connected: true, id: 'other' })], 'a1')).toBe(false)
    expect(newlyConnected([dev({ connected: true })], 'a1')).toBe(true)
  })
})
