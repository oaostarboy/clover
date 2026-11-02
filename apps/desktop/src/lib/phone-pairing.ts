// Builds the `clover://pair` link the Clover iPhone app scans. Rules mirror the
// iOS parser (clover-ios-chat src/lib/qr-pairing.ts): strict https origin with
// no credentials/path/query/fragment, token must be >= 6 chars, both values
// URL-encoded.

export interface PhonePairingInfo {
  configured: boolean
  origin: null | string
  reason?: null | string
  token: null | string
}

export function strictHttpsOrigin(input: string): string {
  let parsed: URL

  try {
    parsed = new URL(input.trim())
  } catch {
    throw new Error('That is not a valid gateway address.')
  }

  if (parsed.protocol !== 'https:') {
    throw new Error('Pairing needs an https:// gateway address.')
  }

  if (parsed.username || parsed.password || parsed.pathname !== '/' || parsed.search || parsed.hash) {
    throw new Error('Gateway address must be a plain https origin.')
  }

  return parsed.origin
}

export function buildPairingUri(origin: string, token: string): string {
  if (token.length < 6) {
    throw new Error('The pairing token is too short.')
  }

  return `clover://pair?origin=${encodeURIComponent(strictHttpsOrigin(origin))}&token=${encodeURIComponent(token)}`
}

/** Link for the QR, or null in the "not set up" state (never an http/LAN fallback). */
export function pairingUriFor(info: null | PhonePairingInfo | undefined): null | string {
  if (!info?.configured || !info.origin || !info.token) {
    return null
  }

  try {
    return buildPairingUri(info.origin, info.token)
  } catch {
    return null
  }
}
