import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useRef } from 'react'

import { $firstRunActive, $welcomeReplay, adoptExistingProfile } from '@/store/first-run'
import { type OnboardingContext, refreshOnboarding } from '@/store/onboarding'

import { FirstRunOverlay } from './index'

interface Props {
  enabled: boolean
  onCompleted?: () => void
  profile: string
  requestGateway: OnboardingContext['requestGateway']
}

/**
 * Mounts the one-screen first-run welcome.
 *
 * Existing users are never forced through it: the moment the gateway is up we
 * ask whether a working setup already exists. If so, the welcome steps aside
 * and pet / star map / chat bar stay exactly as they were. A replay from
 * Settings skips that check on purpose.
 */
export function FirstRunHost({ enabled, onCompleted, profile, requestGateway }: Props) {
  const active = useStore($firstRunActive)
  const replay = useStore($welcomeReplay)
  const ref = useRef({ onCompleted, profile, requestGateway })
  ref.current = { onCompleted, profile, requestGateway }

  const ctx = useMemo<OnboardingContext>(
    () => ({
      requestGateway: (...args) => ref.current.requestGateway(...args),
      onCompleted: () => ref.current.onCompleted?.(),
      get profile() {
        return ref.current.profile
      }
    }),
    []
  )

  useEffect(() => {
    if (!active || replay || !enabled) {
      return
    }

    let live = true

    void refreshOnboarding(ctx).then(ready => {
      if (live && ready) {
        adoptExistingProfile()
      }
    })

    return () => {
      live = false
    }
  }, [active, replay, enabled, ctx])

  return (
    <>
      {active && <FirstRunOverlay ctx={ctx} enabled={enabled} />}
    </>
  )
}
