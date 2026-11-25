import { atom } from 'nanostores'

// First-run welcome + hands-on tour. Tiny on purpose: one screen at a time,
// each step finishes when the user actually DOES the thing, and every step
// can be skipped. Whether to open it is decided by the gateway
// (`onboarding.status`, flag lives in config.yaml → onboarding.seen).

export type TourStep = 'welcome' | 'say-hi' | 'model' | 'stop' | 'help' | 'done'
export type TourEvent = 'message' | 'model' | 'interrupt' | 'help'

export const TOUR_STEPS = ['say-hi', 'model', 'stop', 'help'] as const

const EVENT_STEP: Record<TourEvent, TourStep> = {
  help: 'help',
  interrupt: 'stop',
  message: 'say-hi',
  model: 'model'
}

export const $tour = atom<TourStep | null>(null)

export const getTour = () => $tour.get()
export const tourActive = () => $tour.get() !== null

export const nextTourStep = (cur: TourStep): TourStep | null => {
  if (cur === 'welcome') {
    return TOUR_STEPS[0]
  }

  const i = TOUR_STEPS.indexOf(cur as (typeof TOUR_STEPS)[number])

  if (i < 0) {
    return null
  }

  return TOUR_STEPS[i + 1] ?? 'done'
}

export const openTour = () => $tour.set('welcome')
export const closeTour = () => $tour.set(null)

// Enter on the welcome / finish card, or "skip" on a step.
export const advanceTour = () => {
  const cur = $tour.get()

  if (cur) {
    $tour.set(nextTourStep(cur))
  }
}

// Esc: skip just this step. On the welcome card it leaves the tour.
export const skipTourStep = () => {
  const cur = $tour.get()

  if (!cur) {
    return
  }

  $tour.set(cur === 'welcome' ? null : nextTourStep(cur))
}

// The user did something real. Only moves on if it's the step we're waiting for.
export const tourDid = (ev: TourEvent) => {
  const cur = $tour.get()

  if (cur && cur === EVENT_STEP[ev]) {
    $tour.set(nextTourStep(cur))
  }
}

export const slashTourEvent = (name: string): TourEvent | null =>
  name === 'model' ? 'model' : name === 'help' ? 'help' : null
