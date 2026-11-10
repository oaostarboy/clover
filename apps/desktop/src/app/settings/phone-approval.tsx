import { useEffect, useMemo, useRef, useState } from 'react'

import { cloverApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import {
  capsFromRights,
  initialRights,
  type PairedDevice,
  PHONE_RIGHTS,
  type PhoneRightId
} from '@/lib/phone-pairing'
import { notifyError } from '@/store/notifications'

const POLL_MS = 4000

export const pendingRequest = (devices: PairedDevice[]) => devices.find(d => d.pending_request) ?? null

/** One switch per right. Shared by the approval sheet and the per-device controls. */
export function RightsList({
  disabled,
  onChange,
  rights
}: {
  disabled?: boolean
  onChange: (next: Set<PhoneRightId>) => void
  rights: ReadonlySet<PhoneRightId>
}) {
  return (
    <ul className="divide-y divide-(--stroke-clover)">
      {PHONE_RIGHTS.map(right => {
        const on = rights.has(right.id)

        return (
          <li className="flex items-center gap-3 py-2.5" key={right.id}>
            <div className="min-w-0 flex-1">
              <div className="text-[length:var(--conversation-text-font-size)] text-foreground">{right.label}</div>
              {right.hint ? (
                <div className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
                  {right.hint}
                </div>
              ) : null}
            </div>
            <Switch
              aria-label={right.label}
              checked={on}
              disabled={disabled}
              onCheckedChange={checked => {
                const next = new Set(rights)

                if (checked) {
                  next.add(right.id)

                  // Implied rights switch on with their parent, like the gateway grants them.
                  if (right.id === 'crew_act') {
                    next.add('crew_read')
                  }

                  if (right.id === 'control_drive') {
                    next.add('control_view')
                  }
                } else {
                  next.delete(right.id)

                  if (right.id === 'crew_read') {
                    next.delete('crew_act')
                  }

                  if (right.id === 'control_view') {
                    next.delete('control_drive')
                  }
                }

                onChange(next)
              }}
              size="xs"
            />
          </li>
        )
      })}
    </ul>
  )
}

function ApprovalSheet({ device, onDone }: { device: PairedDevice; onDone: () => void }) {
  const request = device.pending_request!
  const [rights, setRights] = useState<Set<PhoneRightId>>(() => initialRights(request))
  const [busy, setBusy] = useState(false)
  // Off by default: trusting a phone is a deliberate choice, never a side effect of approving.
  const [trust, setTrust] = useState(false)

  async function decide(approve: boolean) {
    setBusy(true)

    try {
      await cloverApi({
        body: approve ? { capabilities: capsFromRights(rights), request_id: request.request_id, trust } : { request_id: request.request_id },
        method: 'POST',
        path: `/api/mobile/devices/${device.id}/${approve ? 'approve' : 'deny'}`
      })
      onDone()
    } catch (err) {
      notifyError(err, approve ? 'Could not approve this iPhone' : 'Could not deny this iPhone')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog onOpenChange={open => !open && !busy && onDone()} open>
      <DialogContent className="max-w-sm" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>Approve this iPhone?</DialogTitle>
          <DialogDescription>{device.name}</DialogDescription>
        </DialogHeader>
        <div className="flex items-baseline justify-between rounded-lg bg-(--ui-bg-quaternary) px-4 py-3">
          <span className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
            Code on your phone
          </span>
          <span className="text-2xl font-medium tabular-nums tracking-widest text-foreground" data-testid="match-code">
            {request.match_code}
          </span>
        </div>
        <RightsList disabled={busy} onChange={setRights} rights={rights} />
        <div className="flex items-center gap-3 border-t border-(--stroke-clover) pt-2.5">
          <div className="min-w-0 flex-1">
            <div className="text-[length:var(--conversation-text-font-size)] text-foreground">Trust this phone</div>
            <div className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
              Trusted: no repeat approvals. Revoke anytime.
            </div>
          </div>
          <Switch aria-label="Trust this phone" checked={trust} disabled={busy} onCheckedChange={setTrust} size="xs" />
        </div>
        <DialogFooter>
          <Button disabled={busy} onClick={() => void decide(false)} variant="secondary">
            Deny
          </Button>
          <Button disabled={busy} onClick={() => void decide(true)}>
            Approve
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * Mounted once in the shell. Polls the gateway while the window is open (Settings
 * need not be), raises a native popup when a phone asks for access, and shows the
 * approval sheet. A request already answered elsewhere drops out on the next poll.
 */
export function PhoneApprovalHost() {
  const [devices, setDevices] = useState<PairedDevice[]>([])
  const [dismissed, setDismissed] = useState<null | string>(null)
  const announced = useRef(new Set<string>())

  useEffect(() => {
    let live = true

    const tick = () => {
      cloverApi<{ devices: PairedDevice[] }>({ path: '/api/mobile/devices' })
        .then(res => live && setDevices(res.devices))
        .catch(() => undefined)
    }

    tick()
    const id = window.setInterval(tick, POLL_MS)

    return () => {
      live = false
      window.clearInterval(id)
    }
  }, [])

  const device = useMemo(() => pendingRequest(devices), [devices])
  const request = device?.pending_request

  useEffect(() => {
    if (!device || !request || announced.current.has(request.request_id)) {
      return
    }

    announced.current.add(request.request_id)
    void window.cloverDesktop?.notify?.({
      body: `${device.name} · code ${request.match_code}`,
      kind: 'phone-access',
      tag: request.request_id,
      title: 'Approve this iPhone?'
    })
  }, [device, request])

  if (!device || !request || dismissed === request.request_id) {
    return null
  }

  return <ApprovalSheet device={device} key={request.request_id} onDone={() => setDismissed(request.request_id)} />
}
