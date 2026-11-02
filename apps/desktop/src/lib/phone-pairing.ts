// Types + display helpers for iPhone pairing. The gateway builds the clover://pair
// link itself (per-phone device token); the desktop never sees the master key.

/** Setup state from GET /api/mobile/pairing. Never carries a credential. */
import { relativeTime } from '@/lib/time'

export interface PhonePairingStatus {
  configured: boolean
  origin: null | string
  reason?: null | string
}

/** A freshly minted pairing code from POST /api/mobile/pairing (the only time the link exists). */
export interface PhonePairingCode extends PhonePairingStatus {
  device?: PairedDevice
  expires_in?: number
  uri?: string
}

export interface PairedDevice {
  connected: boolean
  created_at: number
  id: string
  last_seen_at: null | number
  name: string
}

/** "9:42" for a remaining-seconds count; never negative. */
export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds))

  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** "Last active 2 min ago", or "Not connected yet" for a device that never reached the gateway. */
export function lastActiveLabel(device: Pick<PairedDevice, 'last_seen_at'>, nowMs = Date.now()): string {
  if (device.last_seen_at == null) {
    return 'Not connected yet'
  }

  const diff = Math.max(0, nowMs - device.last_seen_at * 1000)

  if (diff < 60_000) {
    return 'Active just now'
  }

  return `Last active ${relativeTime(device.last_seen_at * 1000, nowMs)}`
}

/** True once the device minted for the shown code has reached the gateway, i.e. the phone scanned it. */
export function newlyConnected(devices: PairedDevice[], deviceId: string): boolean {
  return devices.some(d => d.id === deviceId && d.connected)
}
