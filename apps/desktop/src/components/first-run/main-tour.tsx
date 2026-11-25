import { useStore } from '@nanostores/react'
import { useEffect, useLayoutEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { $mainTourActive, endMainTour } from '@/store/first-run'

// A three-stop tour that points at the REAL main screen (no mock-ups):
// new chat, the chat list, and the "More" door where everything else lives.
// Anchors are the existing `data-tour` handles. If an anchor is missing the
// stop is skipped, so the tour can never get stuck.

interface Stop {
  anchor: string
  body: string
  title: string
}

export const TOUR_STOPS: readonly Stop[] = [
  { anchor: 'sidebar-nav-new-session', title: 'Start a chat', body: 'Click here any time to ask Clover something new.' },
  { anchor: 'sessions-sidebar', title: 'Your chats live here', body: 'Every chat is saved. Pick one to carry on where you left off.' },
  { anchor: 'sidebar-more', title: 'Everything else is under More', body: 'Skills, schedules, messaging and settings are all here, out of your way.' }
]

export const TOUR_FINISH = 'You struck gold! 🍀'

const GAP = 14

function useAnchorRect(anchor: string | undefined) {
  const [rect, setRect] = useState<DOMRect | null>(null)

  useLayoutEffect(() => {
    if (!anchor) {
      return
    }

    const measure = () => {
      const el = document.querySelector<HTMLElement>(`[data-tour="${anchor}"]`)
      setRect(el ? el.getBoundingClientRect() : null)
    }

    measure()
    window.addEventListener('resize', measure)
    const id = window.setInterval(measure, 400)

    return () => {
      window.removeEventListener('resize', measure)
      window.clearInterval(id)
    }
  }, [anchor])

  return rect
}

export function MainTour() {
  const active = useStore($mainTourActive)
  const [index, setIndex] = useState(0)
  const [done, setDone] = useState(false)
  const stop = TOUR_STOPS[index]
  const rect = useAnchorRect(active && !done ? stop?.anchor : undefined)

  // Missing anchor: skip ahead rather than point at nothing.
  useEffect(() => {
    if (!active || done) {
      return
    }

    const id = window.setTimeout(() => {
      if (!document.querySelector(`[data-tour="${stop?.anchor}"]`)) {
        next()
      }
    }, 900)

    return () => window.clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, index, done])

  useEffect(() => {
    if (!active) {
      setIndex(0)
      setDone(false)
    }
  }, [active])

  useEffect(() => {
    if (!active) {
      return
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        end()
      } else if (e.key === 'ArrowRight' || (e.key === 'Enter' && !(e.target instanceof HTMLButtonElement))) {
        // A focused button already turns Enter into a click; don't advance twice.
        next()
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, index, done])

  function end() {
    endMainTour()
  }

  function next() {
    if (index >= TOUR_STOPS.length - 1) {
      setDone(true)
    } else {
      setIndex(i => i + 1)
    }
  }

  if (!active) {
    return null
  }

  if (done) {
    return (
      <div className="fixed inset-0 z-(--z-onboarding) grid place-items-center bg-background/70 backdrop-blur-xl" data-main-tour="">
        <div className="fr-step w-[26rem] max-w-[90vw] rounded-2xl border border-(--ui-stroke-secondary) bg-(--ui-chat-bubble-background) p-7 text-center shadow-2xl">
          <div className="text-2xl font-semibold tracking-tight">{TOUR_FINISH}</div>
          <p className="mt-2 text-sm leading-6 text-(--ui-text-secondary)">
            You’re ready. Ask Clover anything. Settings → About has “Show welcome again” if you want this back.
          </p>
          <Button className="mt-5 px-6" onClick={end} size="lg">
            Let’s go
          </Button>
        </div>
      </div>
    )
  }

  if (!rect || !stop) {
    return null
  }

  // Tall anchors (the whole chat rail) hold the card inside themselves so it
  // never covers the composer; small anchors get it to their right, or below.
  const tall = rect.height > 240
  const width = tall ? Math.min(288, rect.width - 24) : 288
  const fitsRight = !tall && rect.right + GAP + width < window.innerWidth
  const top = tall ? rect.top + rect.height * 0.38 : fitsRight ? Math.max(12, rect.top + rect.height / 2 - 52) : rect.bottom + GAP
  const left = tall ? rect.left + 12 : fitsRight ? rect.right + GAP : Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))

  return (
    <div className="pointer-events-none fixed inset-0 z-(--z-onboarding)" data-main-tour="">
      <div
        aria-hidden
        className="fr-ring absolute rounded-lg ring-2 ring-primary"
        style={{ height: rect.height + 8, left: rect.left - 4, top: rect.top - 4, width: rect.width + 8 }}
      />
      <div
        className={cn('fr-step pointer-events-auto absolute rounded-2xl border border-(--ui-stroke-secondary) bg-(--ui-chat-bubble-background) p-4 shadow-2xl')}
        role="dialog"
        style={{ left, top, width }}
      >
        <div className="text-sm font-semibold">{stop.title}</div>
        <p className="mt-1 text-[0.8125rem] leading-5 text-(--ui-text-secondary)">{stop.body}</p>
        <div className="mt-3 flex items-center justify-between">
          <span className="text-xs text-(--ui-text-tertiary)">
            {index + 1} of {TOUR_STOPS.length}
          </span>
          <div className="flex items-center gap-3">
            <button className="text-xs text-(--ui-text-tertiary) hover:text-foreground" onClick={end} type="button">
              Skip
            </button>
            <Button onClick={next} size="sm">
              {index === TOUR_STOPS.length - 1 ? 'Finish' : 'Next'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
