import { useStore } from '@nanostores/react'
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { cloverApi } from '@/api/client'
import { PhonePairingQr } from '@/app/settings/phone-pairing-section'
import { saveCloverConfig } from '@/clover'
import { ApiKeyForm, providerTitle, sortProviders } from '@/components/onboarding'
import { FlowPanel, Status } from '@/components/onboarding/flow'
import { Button } from '@/components/ui/button'
import { CopyButton } from '@/components/ui/copy-button'
import { Input } from '@/components/ui/input'
import { Check, Loader2 } from '@/lib/icons'
import {
  capsFromRights,
  formatCountdown,
  initialRights,
  type PairedDevice,
  type PhoneAccessRequest,
  type PhonePairingCode,
  type PhonePairingStatus
} from '@/lib/phone-pairing'
import { $agentName, finishFirstRun } from '@/store/first-run'
import { notify, notifyError } from '@/store/notifications'
import {
  $desktopOnboarding,
  type OnboardingContext,
  refreshOnboarding,
  saveOnboardingApiKey,
  setOnboardingMode,
  startProviderOAuth
} from '@/store/onboarding'

import { CloverStar } from './clover-star'

// Plain words. No jargon on the main path.
export const COPY = {
  title: 'Welcome to Clover',
  line: 'Three small things, then we’re off. Luck favors the prepared.',
  nameTitle: 'What should I answer to?',
  nameLine: 'Clover works too. Easier than finding a four-leaf one.',
  namePlaceholder: 'Luna',
  aiTitle: 'Which AI should I think with?',
  aiLine: 'Sign in with the one you already use.',
  start: 'Start',
  startNeed: 'Sign in with one AI to start.',
  startWithout: 'Start without an AI',
  aiOther: 'Another AI',
  aiKeyHint: 'Paste a key from the AI company. It stays on this computer.',
  phoneTitle: 'Connect your phone',
  phoneLine: 'Scan this with Clover on your phone, or type the code.',
  phoneSkip: 'Skip for now',
  phoneLater: 'You can do this any time in Settings → Phone.',
  next: 'Continue',
  keepClover: 'Keep Clover',
  skip: 'Skip for now',
  back: 'Back'
}

/** One short luck line when the main screen appears. */
export const LANDING_LINE = 'Your luck just logged on. Ask me anything.'

export function previewGreeting(name: string): string {
  const n = name.trim() || 'Luna'

  return `Hi, I’m ${n}`
}

// ── Layout ──────────────────────────────────────────────────────────────────

const WORDMARK: Record<string, string[]> = {
  C: ['.####', '#....', '#....', '#....', '#....', '#....', '.####'],
  L: ['#....', '#....', '#....', '#....', '#....', '#....', '#####'],
  O: ['.###.', '#...#', '#...#', '#...#', '#...#', '#...#', '.###.'],
  V: ['#...#', '#...#', '#...#', '#...#', '#...#', '.#.#.', '..#..'],
  E: ['#####', '#....', '#....', '####.', '#....', '#....', '#####'],
  R: ['####.', '#...#', '#...#', '####.', '#.#..', '#..#.', '#...#']
}

/** The solid CLOVER block-letter wordmark, #92f2ba → #24aa6d. */
export function CloverWordmark({ height = 14 }: { height?: number }) {
  const rects: ReactNode[] = []

  ;[...'CLOVER'].forEach((ch, k) => {
    WORDMARK[ch].forEach((row, y) => {
      let x = 0

      while (x < 5) {
        if (row[x] !== '#') {
          x++

          continue
        }

        let e = x

        while (e < 5 && row[e] === '#') {
          e++
        }

        rects.push(<rect height={1} key={`${k}-${y}-${x}`} width={e - x} x={k * 6 + x} y={y} />)
        x = e
      }
    })
  })

  return (
    <svg aria-label="Clover" height={height} role="img" style={{ display: 'block', flex: 'none' }} viewBox="0 0 35 7" width={(height * 35) / 7}>
      <defs>
        <linearGradient gradientUnits="userSpaceOnUse" id="fr-wordmark" x1="0" x2="35" y1="0" y2="0">
          <stop offset="0" stopColor="#92f2ba" />
          <stop offset="1" stopColor="#24aa6d" />
        </linearGradient>
      </defs>
      <g fill="url(#fr-wordmark)" stroke="url(#fr-wordmark)" strokeLinejoin="round" strokeWidth={0.4}>
        {rects}
      </g>
    </svg>
  )
}

const POLL_MS = 2000

/** The relay says 429 when too many new installs come from one network. */
export const isBusyError = (e: unknown) => /\b429\b/.test(e instanceof Error ? e.message : String(e ?? ''))

export const BUSY_LINE = 'Lots of people are connecting right now. Try again in a minute.'

/** The pairing code a phone shows when it asks to connect. */
export const matchCodeLine = (request: PhoneAccessRequest | undefined) => (request ? `Check it shows ${request.match_code}` : '')

export function PhoneConnect({ agentName }: { agentName?: string } = {}) {
  const [status, setStatus] = useState<null | PhonePairingStatus>(null)
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [code, setCode] = useState<null | PhonePairingCode>(null)
  const [expiresAt, setExpiresAt] = useState(0)
  const [now, setNow] = useState(Date.now())
  const [connected, setConnected] = useState(false)
  const [pending, setPending] = useState<null | PairedDevice>(null)
  const [approving, setApproving] = useState(false)
  const codeRef = useRef<null | PhonePairingCode>(null)
  codeRef.current = code
  const name = useStore($agentName) || agentName || 'Clover'

  const newCode = useCallback(async () => {
    setConnected(false)
    setPending(null)
    setCode(null)

    try {
      const res = await cloverApi<PhonePairingCode>({ method: 'POST', path: '/api/mobile/pairing' })
      setCode(res)
      setExpiresAt(Date.now() + (res.expires_in ?? 600) * 1000)
      setNow(Date.now())
    } catch (e) {
      setBusy(isBusyError(e))
      setFailed(true)
    }
  }, [])

  const [attempt, setAttempt] = useState(0)

  const retry = () => {
    setFailed(false)
    setBusy(false)
    setStatus(null)
    setAttempt(n => n + 1)
  }

  useEffect(() => {
    let live = true

    cloverApi<PhonePairingStatus>({ path: '/api/mobile/pairing' })
      .then(s => {
        if (!live) {
          return
        }

        setStatus(s)

        if (s.configured) {
          void newCode()
        }
      })
      .catch(e => {
        if (live) {
          setBusy(isBusyError(e))
          setFailed(true)
        }
      })

    return () => {
      live = false
    }
  }, [newCode, attempt])

  useEffect(() => {
    if (!status?.configured) {
      return
    }

    const id = window.setInterval(() => {
      setNow(Date.now())

      if (document.hidden || !codeRef.current?.device) {
        return
      }

      cloverApi<{ devices: PairedDevice[] }>({ path: '/api/mobile/devices' })
        .then(res => {
          const mine = codeRef.current?.device?.id
          const dev = res.devices.find(d => d.id === mine)

          if (dev?.connected && !dev.pending_request) {
            setConnected(true)
            setPending(null)
          } else {
            setPending(dev?.pending_request ? dev : null)
          }
        })
        .catch(() => undefined)
    }, POLL_MS)

    return () => window.clearInterval(id)
  }, [status?.configured])

  const approve = async () => {
      if (!pending || !request) {
      return
    }

    setApproving(true)

    try {
      await cloverApi({
        body: { capabilities: capsFromRights(initialRights(request)), request_id: request.request_id, trust: false },
        method: 'POST',
        path: `/api/mobile/devices/${pending.id}/approve`
      })
      setConnected(true)
      setPending(null)
    } catch (e) {
      notifyError(e, 'Could not approve this iPhone')
    } finally {
      setApproving(false)
    }
  }

  const remaining = Math.max(0, (expiresAt - now) / 1000)
  const expired = Boolean(code) && !connected && remaining <= 0
  const request = pending?.pending_request
  const unavailable = failed || (status !== null && !status.configured)

  return (
    <div className="w-full" data-testid="phone-connect">
      {unavailable ? (
        <div className="flex items-center gap-4 text-sm text-(--ui-text-secondary)">
          <span>{busy ? BUSY_LINE : 'Phone link isn’t ready on this computer yet.'} {COPY.phoneLater}</span>
          <Button onClick={retry} size="sm" variant="secondary">
            Try again
          </Button>
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-(--stroke-clover) bg-(--ui-bg-quaternary)">
          <div className="flex items-center gap-6 p-5">
            <div className="flex size-40 shrink-0 items-center justify-center rounded-xl p-1.5" data-testid="phone-qr-tile" style={{ background: '#f3f1ea' }}>
              {code?.uri && !expired && !connected ? (
                <PhonePairingQr uri={code.uri} />
              ) : (
                <span className="px-4 text-center text-sm" style={{ color: '#16201d' }}>
                  {connected ? 'Connected' : expired ? 'This code expired.' : 'Making your code…'}
                </span>
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-[0.8125rem] text-(--ui-text-secondary)">One-time code</div>
              <div className="mt-1 text-base text-foreground tabular-nums">
                {connected ? 'Phone connected' : code && !expired ? `Good for ${formatCountdown(remaining)}` : expired ? 'Make a new one' : '…'}
              </div>
              <div className="mt-4 flex items-center gap-3">
                {code?.uri && !connected && !expired ? (
                  <CopyButton appearance="button" buttonVariant="outline" label="Copy code" showLabel text={code.uri} />
                ) : null}
                <button className="text-sm text-(--ui-text-secondary) hover:text-foreground" onClick={() => void newCode()} type="button">
                  {connected ? 'Pair another phone' : 'New code'}
                </button>
              </div>
            </div>
          </div>
          {(request || connected) && (
            <div className="flex items-center gap-4 border-t border-(--stroke-clover) px-5 py-3.5" role="status">
              <div className="min-w-0 flex-1">
                <div className="text-[0.9375rem] font-medium">
                  {connected ? `${name}’s iPhone is connected` : `${name}’s iPhone wants to connect`}
                </div>
                {request && <div className="text-[0.8125rem] text-(--ui-text-secondary)">{matchCodeLine(request)}</div>}
              </div>
              {request && (
                <Button disabled={approving} onClick={() => void approve()}>
                  {approving ? <Loader2 className="animate-spin" /> : null}
                  Approve
                </Button>
              )}
              {connected && <Check className="size-5 text-primary" />}
            </div>
          )}
        </div>
      )}
    </div>
  )
}


function Section({ children, label }: { children: ReactNode; label: string }) {
  return (
    <section className="grid grid-cols-[8.5rem_1fr] gap-6 border-t border-(--stroke-clover) py-6">
      <h2 className="pt-1 text-sm text-(--ui-text-secondary)">{label}</h2>
      <div className="min-w-0">{children}</div>
    </section>
  )
}

function Row({ action, label, onClick, sub }: { action: string; label: string; onClick: () => void; sub: string }) {
  return (
    <div className="flex min-h-14 items-center gap-4 py-1.5">
      <div className="min-w-0 flex-1">
        <div className="text-base leading-[22px] font-medium">{label}</div>
        <div className="text-[0.8125rem] leading-[18px] text-(--ui-text-secondary)">{sub}</div>
      </div>
      <Button onClick={onClick} variant="outline">
        {action}
      </Button>
    </div>
  )
}

export function FirstRunScreen({ ctx, enabled }: { ctx: OnboardingContext; enabled: boolean }) {
  const onboarding = useStore($desktopOnboarding)
  const { flow, providers, mode } = onboarding
  const connected = onboarding.configured === true && !onboarding.manual
  const name = useStore($agentName)
  const [keyForm, setKeyForm] = useState(false)
  const [others, setOthers] = useState(false)
  const [phone, setPhone] = useState(false)

  useEffect(() => {
    if (enabled) {
      void refreshOnboarding(ctx)
    }
  }, [ctx, enabled])

  const ordered = useMemo(() => (providers ? sortProviders(providers) : []), [providers])
  const chatgpt = ordered.find(p => p.id === 'openai-codex')
  const claude = ordered.find(p => p.id === 'claude-code')
  const rest = ordered.filter(p => p !== chatgpt && p !== claude)
  const showFlow = flow.status !== 'idle'

  const apiKeyOptions = useMemo(
    () => [
      { id: 'openrouter', name: 'OpenRouter', envKey: 'OPENROUTER_API_KEY', docsUrl: 'https://openrouter.ai/keys' },
      { id: 'openai', name: 'OpenAI', envKey: 'OPENAI_API_KEY', docsUrl: 'https://platform.openai.com/api-keys' },
      { id: 'gemini', name: 'Google Gemini', envKey: 'GEMINI_API_KEY', docsUrl: 'https://aistudio.google.com/app/apikey' },
      { id: 'local', name: 'On this computer (Ollama)', envKey: 'OPENAI_BASE_URL', docsUrl: '', placeholder: 'http://127.0.0.1:11434/v1' }
    ],
    []
  )

  const start = async () => {
    const final = name.trim() || 'Clover'

    $agentName.set(final)
    // Same two writes the old onboarding made, so the name works everywhere.
    void ctx.requestGateway('config.set', { key: 'agent_name', value: final }).catch(() => undefined)
    await saveCloverConfig({ agent: { name: final }, branding: { agent_name: final } }, ctx.profile).catch(() => undefined)
    finishFirstRun()
    notify({ kind: 'success', message: LANDING_LINE, durationMs: 6000 })
  }

  return (
    <div className="fr-step relative z-10 w-full max-w-[46rem] px-8">
      <h1 className="text-[2rem] leading-10 font-medium tracking-tight">{COPY.title}</h1>
      <p className="mt-1 text-base leading-6 text-(--ui-text-secondary)">{COPY.line}</p>

      <div className="mt-8">
        <Section label="Name">
          <Input
            aria-label="Your Clover’s name"
            autoFocus
            className="h-11 rounded-xl px-4 text-base"
            maxLength={24}
            onChange={e => $agentName.set(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.nativeEvent.isComposing && connected && void start()}
            placeholder={COPY.namePlaceholder}
            value={name}
          />
          <div className="mt-2 flex items-center gap-2 text-[0.8125rem] text-(--ui-text-secondary)" data-testid="name-preview">
            <CloverStar className="size-4" />
            <span>
              {previewGreeting(name)}. {COPY.nameLine}
            </span>
          </div>
        </Section>

        <Section label="AI">
          {connected ? (
            <div className="flex min-h-14 items-center gap-2 text-base text-primary" role="status">
              <Check className="size-4" /> Connected. Nice.
            </div>
          ) : showFlow ? (
            <div className="rounded-xl border border-(--stroke-clover) bg-(--ui-bg-quaternary) p-4 text-left">
              <FlowPanel ctx={ctx} flow={flow} leaving={false} onBegin={() => undefined} />
            </div>
          ) : keyForm ? (
            <ApiKeyForm
              canGoBack
              onBack={() => {
                setKeyForm(false)
                setOnboardingMode('oauth')
              }}
              onSave={(envKey, value, nm, apiKey) => saveOnboardingApiKey(envKey, value, nm, ctx, apiKey)}
              options={apiKeyOptions}
            />
          ) : providers === null && mode !== 'apikey' ? (
            <Status>Looking for ways to sign in…</Status>
          ) : (
            <div>
              {chatgpt && <Row action="Sign in" label="ChatGPT" onClick={() => void startProviderOAuth(chatgpt, ctx)} sub="Use your ChatGPT plan" />}
              {claude && <Row action="Sign in" label="Claude" onClick={() => void startProviderOAuth(claude, ctx)} sub="Use your Claude plan" />}
              <Row
                action={others ? 'Hide' : 'Choose'}
                label="Another AI"
                onClick={() => setOthers(v => !v)}
                sub="Gemini, OpenRouter, your own key, or one on this computer"
              />
              {others && (
                <div className="mb-1 ml-1 grid gap-0.5">
                  {rest.map(p => (
                    <button
                      className="rounded-lg px-3 py-2 text-left text-sm hover:bg-(--ui-control-hover-background)"
                      key={p.id}
                      onClick={() => void startProviderOAuth(p, ctx)}
                      type="button"
                    >
                      {providerTitle(p)}
                    </button>
                  ))}
                  <button
                    className="rounded-lg px-3 py-2 text-left text-sm text-(--ui-text-secondary) hover:bg-(--ui-control-hover-background)"
                    onClick={() => setKeyForm(true)}
                    type="button"
                  >
                    Paste a key instead
                  </button>
                </div>
              )}
            </div>
          )}
        </Section>

        <Section label="Phone">
          {phone ? (
            <PhoneConnect />
          ) : (
            <Row action="Set up" label="Use Clover on your iPhone" onClick={() => setPhone(true)} sub="Scan a one-time code. Optional, and it works later too." />
          )}
        </Section>
      </div>

      <div className="flex items-center justify-between border-t border-(--stroke-clover) pt-6">
        <button className="text-sm text-(--ui-text-secondary) hover:text-foreground" onClick={() => finishFirstRun()} type="button">
          {COPY.startWithout}
        </button>
        <div className="flex items-center gap-4">
          {!connected && <span className="text-[0.8125rem] text-(--ui-text-secondary)">{COPY.startNeed}</span>}
          <Button className="h-10 px-6" disabled={!connected} onClick={() => void start()}>
            {COPY.start}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ── Overlay ─────────────────────────────────────────────────────────────────

export function FirstRunOverlay({ ctx, enabled }: { ctx: OnboardingContext; enabled: boolean }) {
  // Esc leaves the welcome the same way "Start without an AI" does: never a trap.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        finishFirstRun()
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div
      aria-label="Welcome to Clover"
      aria-modal="true"
      className="fixed inset-0 z-(--z-onboarding) flex flex-col items-center justify-center overflow-y-auto bg-(--ui-chat-surface-background) p-6"
      data-first-run=""
      data-glass-opaque=""
      role="dialog"
    >
      <header className="absolute top-0 right-0 left-0 flex h-14 items-center px-8">
        <div className="flex items-center gap-3">
          <CloverStar className="size-5" />
          <CloverWordmark height={13} />
        </div>
      </header>

      <FirstRunScreen ctx={ctx} enabled={enabled} />
    </div>
  )
}
