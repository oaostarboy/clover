import QRCode from 'qrcode'
import { useCallback, useEffect, useState } from 'react'

import { cloverApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { CopyButton } from '@/components/ui/copy-button'
import { Eye, EyeOff, Smartphone } from '@/lib/icons'
import { type PhonePairingInfo, pairingUriFor } from '@/lib/phone-pairing'

import { SettingsSection } from './primitives'

const STEPS = ['Open Clover on your iPhone.', 'Tap Scan.', 'Point it at this code.']

/** One-scan iPhone pairing: a QR of the exact clover://pair link the phone app reads. */
export function PhonePairingQr({ uri }: { uri: string }) {
  const [svg, setSvg] = useState('')

  useEffect(() => {
    let live = true

    // Dark modules on a white quiet zone: phone cameras need the contrast in both themes.
    void QRCode.toString(uri, { errorCorrectionLevel: 'M', margin: 2, type: 'svg', width: 240 }).then(out => {
      if (live) {
        setSvg(out)
      }
    })

    return () => {
      live = false
    }
  }, [uri])

  return (
    <div
      aria-label="QR code for pairing your iPhone"
      className="size-60 shrink-0 bg-white"
      data-testid="phone-pairing-qr"
      dangerouslySetInnerHTML={{ __html: svg }}
      role="img"
    />
  )
}

export function PhonePairingSection() {
  const [info, setInfo] = useState<null | PhonePairingInfo>(null)
  const [failed, setFailed] = useState(false)
  const [showToken, setShowToken] = useState(false)

  const load = useCallback(() => {
    setFailed(false)
    cloverApi<PhonePairingInfo>({ path: '/api/mobile/pairing' })
      .then(setInfo)
      .catch(() => setFailed(true))
  }, [])

  useEffect(load, [load])

  const uri = pairingUriFor(info)

  return (
    <SettingsSection icon={Smartphone} title="Connect your iPhone">
      {failed ? (
        <div className="flex items-center gap-3 py-2 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
          Could not load pairing details.
          <Button onClick={load} size="sm" variant="secondary">
            Try again
          </Button>
        </div>
      ) : !info ? null : !uri ? (
        <p className="py-2 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
          Your iPhone needs a secure https address to reach this gateway, and none is set up yet. Add one with{' '}
          <code className="font-mono">clover config set mobile.public_url https://your-gateway.example.com</code>, then
          come back for your code.
        </p>
      ) : (
        <div className="flex flex-wrap items-start gap-6 py-2">
          <PhonePairingQr uri={uri} />
          <div className="min-w-0 flex-1 basis-56">
            <ol className="list-decimal space-y-1.5 pl-5 text-[length:var(--conversation-text-font-size)] text-foreground">
              {STEPS.map(step => (
                <li key={step}>{step}</li>
              ))}
            </ol>
            <p className="mt-3 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
              The code holds your gateway key. Only show it to your own phone.
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <CopyButton appearance="button" buttonVariant="secondary" label="Copy link" showLabel text={uri} />
              <Button onClick={() => setShowToken(v => !v)} variant="ghost">
                {showToken ? <EyeOff /> : <Eye />}
                {showToken ? 'Hide key' : 'Show key'}
              </Button>
            </div>
            {showToken && info?.token ? (
              <code className="mt-3 block font-mono text-[0.75rem] break-all text-(--ui-text-secondary)">
                {info.token}
              </code>
            ) : null}
          </div>
        </div>
      )}
    </SettingsSection>
  )
}
