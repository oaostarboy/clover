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
  audit?: DeviceAuditEvent[]
  capabilities?: string[]
  connected: boolean
  created_at: number
  effective_capabilities?: string[]
  /** ISO time each timed right lapses (control_drive only). */
  expires?: Record<string, string>
  id: string
  last_seen_at: null | number
  name: string
  pending_request?: PhoneAccessRequest
  status?: 'approved' | 'pending'
  /** Owner-trusted: keeps its rights until removed, no repeat approvals. */
  trusted?: boolean
}

export interface PhoneAccessRequest {
  capabilities: string[]
  created_at: number
  match_code: string
  request_id: string
}

export interface DeviceAuditEvent {
  cap: string
  decision: string
  detail?: string
  device: string
  method: string
  t: string
}

export type PhoneRightId = 'chat' | 'control_drive' | 'control_view' | 'crew_act' | 'crew_read'

/** The five switches the owner sees. `chat` stands for the whole base set the phone had before. */
export const PHONE_RIGHTS: { caps: string[]; hint?: string; id: PhoneRightId; label: string }[] = [
  { caps: ['chat', 'history', 'media', 'model_selection', 'audio_transcribe', 'audio_speak'], id: 'chat', label: 'Chat' },
  { caps: ['crew_read'], id: 'crew_read', label: 'Crew view' },
  { caps: ['crew_read', 'crew_act'], hint: 'Acts as you on this PC', id: 'crew_act', label: 'Crew chat' },
  { caps: ['control_view'], id: 'control_view', label: 'Control view' },
  { caps: ['control_view', 'control_drive'], hint: 'Mouse and keyboard', id: 'control_drive', label: 'Control drive (1 h)' }
]

const DEFAULT_OFF: PhoneRightId[] = ['crew_act', 'control_drive']

/** Which switches are on for a capability list. A right is on when its own (last) capability is held. */
export function rightsFromCaps(caps: readonly string[] | undefined): Set<PhoneRightId> {
  const held = new Set(caps ?? [])

  return new Set(PHONE_RIGHTS.filter(r => held.has(r.caps[r.caps.length - 1]!)).map(r => r.id))
}

/** Capability list for a set of switches. The gateway adds implied rights itself. */
export function capsFromRights(rights: ReadonlySet<PhoneRightId>): string[] {
  return [...new Set(PHONE_RIGHTS.filter(r => rights.has(r.id)).flatMap(r => r.caps))]
}

/** Switches pre-set on the approval sheet: what the phone asked for, except the two powerful ones, which start off. */
export function initialRights(request: PhoneAccessRequest | undefined): Set<PhoneRightId> {
  const asked = rightsFromCaps(request?.capabilities)

  asked.add('chat')

  // Asking to act or drive also asks to look; the look stays on when the powerful part is switched off.
  if (asked.has('crew_act')) {
    asked.add('crew_read')
  }

  if (asked.has('control_drive')) {
    asked.add('control_view')
  }

  for (const id of DEFAULT_OFF) {
    asked.delete(id)
  }

  return asked
}

/** Time left on a timed right, "42 min left", or null when it does not lapse. */
export function timedRightLabel(iso: string | undefined, nowMs = Date.now()): null | string {
  if (!iso) {
    return null
  }

  const left = Date.parse(iso) - nowMs

  if (!Number.isFinite(left) || left <= 0) {
    return 'Expired'
  }

  return `${Math.max(1, Math.ceil(left / 60_000))} min left`
}

/** One audit line: "Crew view allowed · crew.list". Plain words, no ids. */
export function auditLabel(event: DeviceAuditEvent): string {
  const verb = event.decision === 'deny' ? 'denied' : event.decision === 'request' ? 'asked' : 'allowed'
  const name = event.method.startsWith('owner.') ? event.method.slice(6).replace('_', ' ') : event.method

  return `${name} ${verb}`
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
