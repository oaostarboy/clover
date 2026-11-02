import QRCode from 'qrcode'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { cloverApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { CopyButton } from '@/components/ui/copy-button'
import { Check, QrCode, Scan, Smartphone } from '@/lib/icons'
import {
  formatCountdown,
  lastActiveLabel,
  newlyConnected,
  type PairedDevice,
  type PhonePairingCode,
  type PhonePairingStatus
} from '@/lib/phone-pairing'
import { confirm } from '@/store/confirm'
import { notifyError } from '@/store/notifications'

const POLL_MS = 2000
const QUIET_ZONE = 3
const LOGO_FRACTION = 0.22
const assetPath = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`

const STEPS = [
  { icon: Smartphone, text: 'Open Clover on your iPhone' },
  { icon: Scan, text: 'Tap Scan' },
  { icon: QrCode, text: 'Point the camera at this code' }
]

// A phone camera needs dark-on-light in both themes, so the tile colors are fixed, not themed.
const QR_INK = '#16201d'
const QR_PAPER = '#f3f1ea'

/** The pairing link as a QR. Error correction H leaves room for the Clover mark in the centre. */
export function PhonePairingQr({ uri }: { uri: string }) {
  const { path, size } = useMemo(() => {
    const qr = QRCode.create(uri, { errorCorrectionLevel: 'H' })
    const n = qr.modules.size
    const hole = Math.ceil(n * LOGO_FRACTION)
    const lo = Math.floor((n - hole) / 2)
    const hi = lo + hole
    let d = ''

    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        if (qr.modules.get(x, y) && !(x >= lo && x < hi && y >= lo && y < hi)) {
          d += `M${x + QUIET_ZONE} ${y + QUIET_ZONE}h1v1h-1z`
        }
      }
    }

    return { path: d, size: n }
  }, [uri])

  const total = size + QUIET_ZONE * 2
  const logo = Math.ceil(size * LOGO_FRACTION) - 1
  const logoAt = QUIET_ZONE + (size - logo) / 2

  return (
    <svg
      aria-label="QR code for pairing your iPhone"
      className="size-full"
      data-testid="phone-pairing-qr"
      role="img"
      shapeRendering="crispEdges"
      viewBox={`0 0 ${total} ${total}`}
    >
      <path d={path} fill={QR_INK} />
      <image height={logo} href={assetPath('clover-mark.png')} width={logo} x={logoAt} y={logoAt} />
    </svg>
  )
}

function QrTile({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="relative flex size-60 shrink-0 items-center justify-center rounded-2xl p-1.5 shadow-clover"
      style={{ background: QR_PAPER }}
    >
      {children}
    </div>
  )
}

function DeviceRow({ device, onRemove }: { device: PairedDevice; onRemove: (d: PairedDevice) => void }) {
  return (
    <li className="flex items-center gap-3 py-2.5">
      <Smartphone className="size-4 shrink-0 text-(--ui-text-tertiary)" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[length:var(--conversation-text-font-size)] text-foreground">{device.name}</div>
        <div className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
          {lastActiveLabel(device)}
        </div>
      </div>
      <Button onClick={() => onRemove(device)} size="sm" variant="text">
        Remove
      </Button>
    </li>
  )
}

export function PhonePairingSection() {
  const [status, setStatus] = useState<null | PhonePairingStatus>(null)
  const [failed, setFailed] = useState(false)
  const [code, setCode] = useState<null | PhonePairingCode>(null)
  const [expiresAt, setExpiresAt] = useState(0)
  const [now, setNow] = useState(Date.now())
  const [devices, setDevices] = useState<PairedDevice[]>([])
  const [connected, setConnected] = useState(false)
  const codeRef = useRef<null | PhonePairingCode>(null)

  codeRef.current = code

  const newCode = useCallback(async () => {
    const stale = codeRef.current?.device

    setConnected(false)
    setCode(null)

    try {
      const res = await cloverApi<PhonePairingCode>({ method: 'POST', path: '/api/mobile/pairing' })

      setCode(res)
      setExpiresAt(Date.now() + (res.expires_in ?? 600) * 1000)
      setNow(Date.now())

      // A code nobody scanned is dead weight: cut it off rather than wait for it to lapse.
      if (stale && !stale.connected) {
        void cloverApi({ method: 'DELETE', path: `/api/mobile/devices/${stale.id}` }).catch(() => undefined)
      }
    } catch {
      setFailed(true)
    }
  }, [])

  const load = useCallback(() => {
    setFailed(false)
    cloverApi<PhonePairingStatus>({ path: '/api/mobile/pairing' })
      .then(s => {
        setStatus(s)

        if (s.configured) {
          void newCode()
        }
      })
      .catch(() => setFailed(true))
  }, [newCode])

  useEffect(load, [load])

  // Poll devices every 2s, only while this section is mounted and the window is visible.
  useEffect(() => {
    if (!status?.configured) {
      return
    }

    let live = true

    const tick = () => {
      setNow(Date.now())

      if (document.hidden) {
        return
      }

      cloverApi<{ devices: PairedDevice[] }>({ path: '/api/mobile/devices' })
        .then(res => {
          if (!live) {
            return
          }

          setDevices(res.devices)

          const mine = codeRef.current?.device

          if (mine && newlyConnected(res.devices, mine.id)) {
            setConnected(true)
          }
        })
        .catch(() => undefined)
    }

    tick()
    const id = window.setInterval(tick, POLL_MS)

    return () => {
      live = false
      window.clearInterval(id)
    }
  }, [status?.configured])

  async function remove(device: PairedDevice) {
    const ok = await confirm({
      confirmLabel: 'Remove',
      description: 'This phone will be signed out of Clover. You can pair it again any time.',
      destructive: true,
      title: `Remove ${device.name}?`
    })

    if (!ok) {
      return
    }

    try {
      await cloverApi({ method: 'DELETE', path: `/api/mobile/devices/${device.id}` })
      setDevices(list => list.filter(d => d.id !== device.id))
    } catch (err) {
      notifyError(err, 'Could not remove that device')
    }
  }

  const remaining = Math.max(0, (expiresAt - now) / 1000)
  const expired = Boolean(code) && !connected && remaining <= 0
  // The phone for the code on screen isn't a "device" until it connects; hide it from the list until then.
  const paired = devices.filter(d => d.connected && !(code?.device?.id === d.id && !connected))

  return (
    <section className="mb-8">
      {failed ? (
        <div className="flex items-center gap-3 py-2 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
          Could not load pairing details.
          <Button onClick={load} size="sm" variant="secondary">
            Try again
          </Button>
        </div>
      ) : !status ? null : !status.configured ? (
        <div className="rounded-2xl bg-(--ui-bg-quaternary) px-6 py-6">
          <h3 className="text-xl font-semibold tracking-tight text-foreground">Connect your iPhone</h3>
          <p className="mt-1.5 text-[length:var(--conversation-text-font-size)] text-(--ui-text-secondary)">
            One quick step first: give your iPhone a secure address to reach this computer.
          </p>
          <code className="mt-4 block font-mono text-[length:var(--conversation-caption-font-size)] break-all text-(--ui-text-secondary)">
            clover config set mobile.public_url https://your-gateway.example.com
          </code>
          <p className="mt-3 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
            Run that, then come back for your code.
          </p>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-12 gap-y-8 rounded-2xl bg-(--ui-bg-quaternary) px-10 py-10">
            <div className="flex flex-col items-center gap-3">
              <QrTile>
                {connected ? (
                  <div className="flex flex-col items-center gap-3 text-center" data-testid="phone-connected">
                    <span className="flex size-14 items-center justify-center rounded-full bg-primary text-primary-foreground">
                      <Check className="size-7" />
                    </span>
                    <span className="text-base font-semibold" style={{ color: QR_INK }}>
                      iPhone connected
                    </span>
                  </div>
                ) : code?.uri && !expired ? (
                  <PhonePairingQr uri={code.uri} />
                ) : (
                  <span className="px-6 text-center text-sm" style={{ color: QR_INK }}>
                    {expired ? 'This code expired.' : 'Making your code…'}
                  </span>
                )}
              </QrTile>
              <div className="flex h-6 items-center gap-1.5 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
                {connected ? null : code && !expired ? (
                  <>
                    <span className="tabular-nums">Expires in {formatCountdown(remaining)}</span>
                    <span aria-hidden>·</span>
                  </>
                ) : null}
                <Button onClick={() => void newCode()} size="inline" variant="text">
                  {connected ? 'Pair another phone' : 'New code'}
                </Button>
              </div>
            </div>

            <div className="min-w-0 flex-1 basis-64">
              <h3 className="text-3xl font-semibold tracking-tight text-foreground">Connect your iPhone</h3>
              <p className="mt-1.5 text-[length:var(--conversation-text-font-size)] text-(--ui-text-secondary)">
                Open Clover on your iPhone and scan this code. That&apos;s it.
              </p>
              <ul className="mt-7 space-y-3.5">
                {STEPS.map(({ icon: Icon, text }) => (
                  <li
                    className="flex items-center gap-3 text-[length:var(--conversation-text-font-size)] text-foreground"
                    key={text}
                  >
                    <Icon className="size-4 shrink-0 text-(--ui-text-tertiary)" />
                    {text}
                  </li>
                ))}
              </ul>
              {code?.uri && !connected ? (
                <div className="mt-5 -ml-2">
                  <CopyButton appearance="button" buttonVariant="ghost" label="Copy link" showLabel text={code.uri} />
                </div>
              ) : null}
            </div>
          </div>

          <div className="mt-6 px-1">
            <h4 className="text-[length:var(--conversation-text-font-size)] font-medium text-foreground">
              Your devices
            </h4>
            {paired.length ? (
              <ul className="mt-1">
                {paired.map(d => (
                  <DeviceRow device={d} key={d.id} onRemove={device => void remove(device)} />
                ))}
              </ul>
            ) : (
              <p className="mt-1.5 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
                No phones connected yet. Scan the code above to add one.
              </p>
            )}
          </div>
        </>
      )}
    </section>
  )
}
