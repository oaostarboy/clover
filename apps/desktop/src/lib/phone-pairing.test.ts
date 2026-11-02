import { describe, expect, it } from 'vitest'

import { buildPairingUri, pairingUriFor } from './phone-pairing'

const FAKE = 'fake token/with+chars&=1'

// Copy of the iOS parseQrPairingPayload `clover://pair` rules.
function iosParse(raw: string) {
  const url = new URL(raw.trim())
  const origin = url.searchParams.get('origin') || url.searchParams.get('gateway')
  const token = url.searchParams.get('token') || url.searchParams.get('key') || url.searchParams.get('secret')

  if (!/^clover:\/\/pair(\?|$)/i.test(raw) || !origin) {
    throw new Error('bad')
  }

  const o = new URL(origin)

  if (o.protocol !== 'https:' || o.pathname !== '/' || o.search || o.hash || o.username) {
    throw new Error('origin')
  }

  if (!token || token.length < 6) {
    throw new Error('token')
  }

  return { origin: o.origin, token }
}

describe('phone pairing uri', () => {
  it('round-trips through the iOS parser rules', () => {
    const uri = buildPairingUri('https://gateway.example.com/', FAKE)

    expect(uri.startsWith('clover://pair?origin=https%3A%2F%2Fgateway.example.com&token=')).toBe(true)
    expect(iosParse(uri)).toEqual({ origin: 'https://gateway.example.com', token: FAKE })
  })

  it('rejects non-https and non-origin addresses', () => {
    for (const bad of ['http://x.com', 'https://x.com/a', 'https://u:p@x.com', 'x.com']) {
      expect(() => buildPairingUri(bad, 'abcdef')).toThrow()
    }
  })

  it('returns no link in the not-set-up state', () => {
    expect(pairingUriFor(null)).toBeNull()
    expect(pairingUriFor({ configured: false, origin: null, token: null })).toBeNull()
    expect(pairingUriFor({ configured: true, origin: 'http://192.168.1.2', token: 'abcdefgh' })).toBeNull()
  })
})
