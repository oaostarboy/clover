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

describe('grant helpers', () => {
  it('maps switches to capabilities and back, with implied rights', async () => {
    const { capsFromRights, rightsFromCaps, initialRights } = await import('./phone-pairing')
    const caps = capsFromRights(new Set(['chat', 'crew_act']))

    expect(caps).toEqual(expect.arrayContaining(['chat', 'audio_speak', 'crew_read', 'crew_act']))
    expect([...rightsFromCaps(caps)].sort()).toEqual(['chat', 'crew_act', 'crew_read'])
    expect([...initialRights({ capabilities: ['crew_act', 'control_drive'], created_at: 0, match_code: '1', request_id: 'x' })].sort()).toEqual(['chat', 'control_view', 'crew_read'])
  })

  it('labels the timed right', async () => {
    const { timedRightLabel } = await import('./phone-pairing')
    const now = Date.parse('2026-11-09T20:00:00Z')

    expect(timedRightLabel('2026-11-09T20:42:00Z', now)).toBe('42 min left')
    expect(timedRightLabel('2026-11-09T19:00:00Z', now)).toBe('Expired')
    expect(timedRightLabel(undefined, now)).toBeNull()
  })
})
