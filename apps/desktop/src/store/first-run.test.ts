import { beforeEach, describe, expect, it, vi } from 'vitest'

// The install kind is decided once at module load, so each case starts from a
// fresh module and a controlled localStorage.
async function load(seed: Record<string, string> = {}) {
  vi.resetModules()
  window.localStorage.clear()

  for (const [k, v] of Object.entries(seed)) {
    window.localStorage.setItem(k, v)
  }

  return import('./first-run')
}

describe('first-run store', () => {
  beforeEach(() => window.localStorage.clear())

  it('treats an empty profile as new: welcome shows, extras start off', async () => {
    const m = await load()

    expect(m.INSTALL_KIND).toBe('new')
    expect(m.$firstRunActive.get()).toBe(true)
    expect(m.$extraPet.get()).toBe(false)
    expect(m.$extraStarmap.get()).toBe(false)
    expect(m.$extraHud.get()).toBe(false)
    expect(m.$extraWindowControls.get()).toBe(false)
  })

  it('never forces a configured install through it, and keeps its extras', async () => {
    const m = await load({ 'clover-desktop-onboarded-v1': '1' })

    expect(m.INSTALL_KIND).toBe('existing')
    expect(m.$firstRunActive.get()).toBe(false)
    expect(m.$extraPet.get()).toBe(true)
    expect(m.$extraStarmap.get()).toBe(true)
    expect(m.$extraHud.get()).toBe(true)
  })

  it('treats a finished old onboarding (skipped or tour seen) as an existing profile', async () => {
    for (const key of ['clover-onboarding-skipped-v1', 'clover-desktop-tour-v1']) {
      const m = await load({ [key]: '1' })

      expect(m.INSTALL_KIND).toBe('existing')
      expect(m.$firstRunActive.get()).toBe(false)
    }
  })

  it('ignores unrelated boot-time keys other stores write', async () => {
    const m = await load({ 'clover.desktop.sidebarOpen': 'true' })

    expect(m.INSTALL_KIND).toBe('new')
  })

  it('remembers the answer, so later keys do not flip a new install', async () => {
    await load()
    window.localStorage.setItem('clover.desktop.somethingElse', '1')
    vi.resetModules()
    const m = await import('./first-run')

    expect(m.INSTALL_KIND).toBe('new')
  })

  it('shows once: finishing stores the flag', async () => {
    const m = await load()

    m.finishFirstRun()

    expect(m.$welcomeDone.get()).toBe(true)
    expect(m.$firstRunActive.get()).toBe(false)
    expect(window.localStorage.getItem('clover.desktop.welcomeDone')).toBe('true')
  })

  it('can be reopened later, from the first step, without clearing the flag', async () => {
    const m = await load({ 'clover-desktop-onboarded-v1': '1' })

    m.showWelcomeAgain()

    expect(m.$firstRunActive.get()).toBe(true)
    expect(m.$firstRunStep.get()).toBe('setup')
    expect(m.$welcomeDone.get()).toBe(true)

    m.finishFirstRun()

    expect(m.$firstRunActive.get()).toBe(false)
  })

  it('is a single screen: next and previous clamp', async () => {
    const m = await load()

    m.nextFirstRunStep()
    expect(m.$firstRunStep.get()).toBe('setup')
    m.previousFirstRunStep()
    expect(m.$firstRunStep.get()).toBe('setup')
  })

  it('adopting an existing profile turns the extras on and steps the welcome aside', async () => {
    const m = await load()

    m.adoptExistingProfile()

    expect(m.$firstRunActive.get()).toBe(false)
    expect(m.$extraPet.get()).toBe(true)
    expect(m.$extraHud.get()).toBe(true)
  })
})
