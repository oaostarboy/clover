import { useStore } from '@nanostores/react'
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { cloverApi } from '@/api/client'
import { PhonePairingQr } from '@/app/settings/phone-pairing-section'
import { saveCloverConfig } from '@/clover'
import { ApiKeyForm, providerTitle, sortProviders } from '@/components/onboarding'
import { FlowPanel, Status } from '@/components/onboarding/flow'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Check, Loader2 } from '@/lib/icons'
import { formatCountdown, type PairedDevice, type PhonePairingCode, type PhonePairingStatus } from '@/lib/phone-pairing'
import { cn } from '@/lib/utils'
import { $agentName, $firstRunStep, finishFirstRun, FIRST_RUN_STEPS, nextFirstRunStep, previousFirstRunStep } from '@/store/first-run'
import {
  $desktopOnboarding,
  dismissFirstRunOnboarding,
  type OnboardingContext,
  refreshOnboarding,
  saveOnboardingApiKey,
  setOnboardingMode,
  startProviderOAuth
} from '@/store/onboarding'
import type { OAuthProvider } from '@/types/clover'

import { CloverStar } from './clover-star'

// Plain words. No jargon on the main path.
export const COPY = {
  welcomeTitle: 'Hi, I’m Clover.',
  welcomeLine: 'Your own AI helper. It chats, remembers, and gets things done, on your computer and your phone.',
  start: 'Start',
  aiTitle: 'Let’s get Clover thinking',
  aiLine: 'Sign in with a subscription you already have, or pick another AI.',
  aiOther: 'Use another AI',
  aiKeyHint: 'Paste a key from the AI company. It stays on this computer.',
  nameTitle: 'Give your Clover a name',
  nameLine: 'Pick anything you like. You can change it later.',
  namePlaceholder: 'Luna',
  phoneTitle: 'Take Clover with you',
  phoneLine: 'Open Clover on your iPhone and scan this code. That’s it.',
  phoneSkip: 'Skip, do it later',
  phoneLater: 'You can do this any time in Settings → Phone.',
  next: 'Continue',
  back: 'Back'
}

export function previewGreeting(name: string): string {
  const n = name.trim() || 'Luna'

  return `Hi, I’m ${n}.`
}

// ── Layout ──────────────────────────────────────────────────────────────────

function StepShell({ children, step }: { children: ReactNode; step: number }) {
  return (
    <div className="fr-step relative z-10 flex w-full max-w-[28rem] flex-col items-center text-center" key={step}>
      {children}
    </div>
  )
}

function Dots({ index }: { index: number }) {
  return (
    <div aria-hidden className="flex items-center gap-1.5">
      {FIRST_RUN_STEPS.map((id, i) => (
        <span
          className={cn(
            'h-1.5 rounded-full transition-all duration-300',
            i === index ? 'w-5 bg-primary' : i < index ? 'w-1.5 bg-primary/50' : 'w-1.5 bg-foreground/15'
          )}
          key={id}
        />
      ))}
    </div>
  )
}

// ── 1. Welcome ──────────────────────────────────────────────────────────────

function WelcomeStep() {
  return (
    <StepShell step={0}>
      <CloverStar className="fr-star mb-8 size-16" />
      <h1 className="text-[1.75rem] leading-tight font-semibold tracking-tight">{COPY.welcomeTitle}</h1>
      <p className="mt-3 max-w-[22rem] text-[0.9375rem] leading-6 text-(--ui-text-secondary)">{COPY.welcomeLine}</p>
      <Button autoFocus className="mt-9 h-10 px-7 text-sm" onClick={nextFirstRunStep} size="lg">
        {COPY.start}
      </Button>
    </StepShell>
  )
}

// ── 2. Get Clover thinking ──────────────────────────────────────────────────

function AiStep({ ctx }: { ctx: OnboardingContext }) {
  const onboarding = useStore($desktopOnboarding)
  const { flow, providers, mode } = onboarding
  const connected = onboarding.configured === true && !onboarding.manual
  const [keyForm, setKeyForm] = useState(false)

  useEffect(() => {
    void refreshOnboarding(ctx)
  }, [ctx])

  // As soon as a provider is connected (or already was), move on.
  useEffect(() => {
    if (connected && flow.status === 'idle') {
      const id = window.setTimeout(nextFirstRunStep, 700)

      return () => window.clearTimeout(id)
    }
  }, [connected, flow.status])

  const ordered = useMemo(() => (providers ? sortProviders(providers) : []), [providers])
  // ChatGPT first, then Claude, then the rest.
  const subscriptions = ordered.filter(p => p.id === 'openai-codex' || p.id === 'claude-code')
  const others = ordered.filter(p => !subscriptions.includes(p))
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

  return (
    <StepShell step={1}>
      <h2 className="text-2xl font-semibold tracking-tight">{COPY.aiTitle}</h2>
      <p className="mt-2 text-sm leading-6 text-(--ui-text-secondary)">{COPY.aiLine}</p>

      <div className="mt-7 w-full">
        {connected ? (
          <div className="flex items-center justify-center gap-2 text-sm text-primary" role="status">
            <Check className="size-4" /> Clover is thinking. Nice.
          </div>
        ) : showFlow ? (
          <div className="rounded-xl border border-(--ui-stroke-tertiary) bg-(--ui-bg-quaternary) p-4 text-left">
            <FlowPanel ctx={ctx} flow={flow} leaving={false} onBegin={() => undefined} />
          </div>
        ) : keyForm ? (
          <div className="text-left">
            <ApiKeyForm
              canGoBack
              onBack={() => {
                setKeyForm(false)
                setOnboardingMode('oauth')
              }}
              onSave={(envKey, value, name, apiKey) => saveOnboardingApiKey(envKey, value, name, ctx, apiKey)}
              options={apiKeyOptions}
            />
          </div>
        ) : providers === null && mode !== 'apikey' ? (
          <Status>Looking for ways to sign in…</Status>
        ) : (
          <div className="grid gap-2.5">
            {subscriptions.map((p, i) => (
              <ChoiceRow key={p.id} onClick={() => void startProviderOAuth(p, ctx)} primary={i === 0} provider={p} />
            ))}
            {others.length > 0 && (
              <details className="group mt-1 text-left">
                <summary className="cursor-pointer list-none text-center text-xs font-medium text-(--ui-text-tertiary) hover:text-foreground">
                  {COPY.aiOther}
                </summary>
                <div className="mt-2 grid gap-1.5">
                  {others.map(p => (
                    <ChoiceRow key={p.id} onClick={() => void startProviderOAuth(p, ctx)} provider={p} />
                  ))}
                  <button
                    className="rounded-lg px-3 py-2 text-left text-sm text-(--ui-text-secondary) hover:bg-(--ui-control-hover-background)"
                    onClick={() => setKeyForm(true)}
                    type="button"
                  >
                    Paste an API key instead
                  </button>
                </div>
              </details>
            )}
            {others.length === 0 && (
              <Button onClick={() => setKeyForm(true)} variant="outline">
                Paste an API key
              </Button>
            )}
          </div>
        )}
      </div>

      <div className="mt-8 flex items-center gap-4 text-xs text-(--ui-text-tertiary)">
        <button className="hover:text-foreground" onClick={previousFirstRunStep} type="button">
          {COPY.back}
        </button>
        {!connected && (
          <button
            className="hover:text-foreground"
            onClick={() => {
              dismissFirstRunOnboarding()
              nextFirstRunStep()
            }}
            type="button"
          >
            Skip for now
          </button>
        )}
      </div>
    </StepShell>
  )
}

function ChoiceRow({ onClick, primary, provider }: { onClick: () => void; primary?: boolean; provider: OAuthProvider }) {
  const title = provider.id === 'openai-codex' ? 'Continue with ChatGPT' : provider.id === 'claude-code' ? 'Continue with Claude' : providerTitle(provider)

  return (
    <button
      className={cn(
        'flex h-11 w-full items-center justify-center rounded-xl text-sm font-medium transition-colors',
        primary
          ? 'bg-primary text-primary-foreground hover:bg-primary/90'
          : 'border border-(--ui-stroke-secondary) bg-transparent text-foreground hover:bg-(--ui-control-hover-background)'
      )}
      onClick={onClick}
      type="button"
    >
      {title}
    </button>
  )
}

// ── 3. Name your Clover ─────────────────────────────────────────────────────

function NameStep({ ctx }: { ctx: OnboardingContext }) {
  const name = useStore($agentName)
  const [saving, setSaving] = useState(false)
  const greeting = previewGreeting(name)

  const save = async () => {
    const final = name.trim() || 'Clover'

    setSaving(true)
    // Same two writes the old onboarding made, so the name works everywhere.
    void ctx.requestGateway('config.set', { key: 'agent_name', value: final }).catch(() => undefined)
    await saveCloverConfig({ agent: { name: final }, branding: { agent_name: final } }, ctx.profile).catch(() => undefined)
    setSaving(false)
    nextFirstRunStep()
  }

  return (
    <StepShell step={2}>
      <h2 className="text-2xl font-semibold tracking-tight">{COPY.nameTitle}</h2>
      <p className="mt-2 text-sm leading-6 text-(--ui-text-secondary)">{COPY.nameLine}</p>

      <div className="mt-8 flex min-h-14 items-center justify-center gap-3" data-testid="name-preview">
        <CloverStar className="size-7" />
        <span className="text-xl font-medium tracking-tight">{greeting}</span>
      </div>

      <Input
        aria-label="Your Clover’s name"
        autoFocus
        className="mt-4 h-11 text-center text-base"
        maxLength={24}
        onChange={e => $agentName.set(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && !e.nativeEvent.isComposing && void save()}
        placeholder={COPY.namePlaceholder}
        value={name}
      />

      <Button className="mt-6 h-10 px-7 text-sm" disabled={saving} onClick={() => void save()} size="lg">
        {saving ? <Loader2 className="animate-spin" /> : null}
        {COPY.next}
      </Button>
      <button className="mt-4 text-xs text-(--ui-text-tertiary) hover:text-foreground" onClick={previousFirstRunStep} type="button">
        {COPY.back}
      </button>
    </StepShell>
  )
}

// ── 4. Connect your phone ───────────────────────────────────────────────────
// The QR is the EXISTING pairing link: GET/POST /api/mobile/pairing returns
// `uri = clover://pair?origin=<https origin>&token=<per-phone bearer>`, drawn by
// the same PhonePairingQr as Settings → Phone. No new protocol.

const POLL_MS = 2000

function PhoneStep() {
  const [status, setStatus] = useState<null | PhonePairingStatus>(null)
  const [failed, setFailed] = useState(false)
  const [code, setCode] = useState<null | PhonePairingCode>(null)
  const [expiresAt, setExpiresAt] = useState(0)
  const [now, setNow] = useState(Date.now())
  const [connected, setConnected] = useState(false)
  const codeRef = useRef<null | PhonePairingCode>(null)
  codeRef.current = code

  const newCode = useCallback(async () => {
    setConnected(false)
    setCode(null)

    try {
      const res = await cloverApi<PhonePairingCode>({ method: 'POST', path: '/api/mobile/pairing' })
      setCode(res)
      setExpiresAt(Date.now() + (res.expires_in ?? 600) * 1000)
      setNow(Date.now())
    } catch {
      setFailed(true)
    }
  }, [])

  const [attempt, setAttempt] = useState(0)

  const retry = () => {
    setFailed(false)
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
      .catch(() => live && setFailed(true))

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

          if (mine && res.devices.some(d => d.id === mine && d.connected)) {
            setConnected(true)
          }
        })
        .catch(() => undefined)
    }, POLL_MS)

    return () => window.clearInterval(id)
  }, [status?.configured])

  const remaining = Math.max(0, (expiresAt - now) / 1000)
  const expired = Boolean(code) && !connected && remaining <= 0
  const unavailable = failed || (status !== null && !status.configured)

  return (
    <StepShell step={3}>
      <h2 className="text-2xl font-semibold tracking-tight">{COPY.phoneTitle}</h2>
      <p className="mt-2 text-sm leading-6 text-(--ui-text-secondary)">
        {unavailable ? 'Phone link isn’t ready on this computer yet.' : COPY.phoneLine}
      </p>

      {!unavailable && (
        <div className="mt-7 flex flex-col items-center gap-3">
          <div
            className="relative flex size-52 items-center justify-center rounded-2xl p-1.5"
            style={{ background: '#f3f1ea' }}
          >
            {connected ? (
              <div className="flex flex-col items-center gap-2" data-testid="phone-connected">
                <span className="flex size-12 items-center justify-center rounded-full bg-primary text-primary-foreground">
                  <Check className="size-6" />
                </span>
                <span className="text-sm font-semibold" style={{ color: '#16201d' }}>
                  iPhone connected
                </span>
              </div>
            ) : code?.uri && !expired ? (
              <PhonePairingQr uri={code.uri} />
            ) : (
              <span className="px-6 text-center text-sm" style={{ color: '#16201d' }}>
                {expired ? 'This code expired.' : 'Making your code…'}
              </span>
            )}
          </div>
          <div className="flex h-5 items-center gap-1.5 text-xs text-(--ui-text-tertiary)">
            {!connected && code && !expired ? <span className="tabular-nums">Expires in {formatCountdown(remaining)} ·</span> : null}
            <button className="hover:text-foreground" onClick={() => void newCode()} type="button">
              {connected ? 'Pair another phone' : 'New code'}
            </button>
          </div>
        </div>
      )}

      {unavailable && (
        <>
          <p className="mt-4 text-xs text-(--ui-text-tertiary)">{COPY.phoneLater}</p>
          <Button className="mt-4" onClick={retry} size="sm" variant="secondary">
            Try again
          </Button>
        </>
      )}

      <div className="mt-8 flex flex-col items-center gap-3">
        {connected ? (
          <Button className="h-10 px-7 text-sm" onClick={() => finishFirstRun()} size="lg">
            {COPY.next}
          </Button>
        ) : (
          <button
            className="text-sm font-medium text-(--ui-text-secondary) hover:text-foreground"
            onClick={() => finishFirstRun()}
            type="button"
          >
            {COPY.phoneSkip}
          </button>
        )}
        <button className="text-xs text-(--ui-text-tertiary) hover:text-foreground" onClick={previousFirstRunStep} type="button">
          {COPY.back}
        </button>
      </div>
    </StepShell>
  )
}

// ── Overlay ─────────────────────────────────────────────────────────────────

export function FirstRunOverlay({ ctx, enabled }: { ctx: OnboardingContext; enabled: boolean }) {
  const step = useStore($firstRunStep)
  const index = FIRST_RUN_STEPS.indexOf(step)

  // Esc steps back, never traps (every way in needs a way out).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && index > 0) {
        previousFirstRunStep()
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [index])

  return (
    <div
      aria-label="Welcome to Clover"
      aria-modal="true"
      className="fixed inset-0 z-(--z-onboarding) flex flex-col items-center justify-center overflow-y-auto bg-(--ui-chat-surface-background) p-6"
      data-first-run=""
      data-glass-opaque=""
      role="dialog"
    >
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-radial-[circle_at_50%_32%] from-emerald-500/10 via-transparent to-transparent" />

      <div className="absolute top-8 flex w-full justify-center">
        <Dots index={index} />
      </div>

      {step === 'welcome' && <WelcomeStep />}
      {step === 'ai' && (enabled ? <AiStep ctx={ctx} /> : <StepShell step={1}><Status>Getting ready…</Status></StepShell>)}
      {step === 'name' && <NameStep ctx={ctx} />}
      {step === 'phone' && <PhoneStep />}

      {step === 'welcome' && (
        <button
          className="absolute bottom-6 text-xs text-(--ui-text-tertiary) hover:text-foreground"
          onClick={() => finishFirstRun({ tour: false })}
          type="button"
        >
          Skip setup
        </button>
      )}
    </div>
  )
}
