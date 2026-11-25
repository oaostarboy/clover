import { atom, computed } from 'nanostores'

import { Codecs, persistentAtom } from '@/lib/persisted'
import { readKey, writeKey } from '@/lib/storage'

// First-run experience for the desktop app: ONE calm screen (name it, sign in
// to an AI, optionally connect a phone, Start), like T3 Code's first launch, then a short tour on the real main screen that
// ends with "You struck gold".
//
// State is renderer-local on purpose (it is purely this window's presentation):
// localStorage through the storage choke point, scoped by key name.
//
// "Is this a new install?" is decided ONCE, the first time this module loads on
// a profile, and then stored. Anyone who already has a configured provider (the
// old onboarding cache key), or any other Clover desktop key, is an existing
// user: they never see the welcome, and they keep pet / starmap / HUD exactly
// as they were. A truly empty profile is a new install: the extras start off
// and each has a switch in Settings → Extras.

const INSTALL_KIND_KEY = 'clover.desktop.installKind'
const WELCOME_DONE_KEY = 'clover.desktop.welcomeDone'

export type InstallKind = 'existing' | 'new'

// Strong signals only. Other stores write their own `clover.desktop.*` keys at
// boot (theme, layout...), possibly before this module loads, so those prove
// nothing. What proves "has used Clover before": the old onboarding finished
// (configured / skipped / tour seen). Anyone configured on the backend side is
// caught a moment later by adoptExistingProfile() once the gateway answers.
const LEGACY_SIGNALS = ['clover-desktop-onboarded-v1', 'clover-onboarding-skipped-v1', 'clover-desktop-tour-v1']

function looksLikeExistingProfile(): boolean {
  return readKey(WELCOME_DONE_KEY) !== null || LEGACY_SIGNALS.some(key => readKey(key) === '1')
}

function resolveInstallKind(): InstallKind {
  const stored = readKey(INSTALL_KIND_KEY)

  if (stored === 'new' || stored === 'existing') {
    return stored
  }

  const kind: InstallKind = looksLikeExistingProfile() ? 'existing' : 'new'
  writeKey(INSTALL_KIND_KEY, kind)

  return kind
}

export const INSTALL_KIND: InstallKind = typeof window === 'undefined' ? 'existing' : resolveInstallKind()

// ── Welcome flow ────────────────────────────────────────────────────────────

export type FirstRunStep = 'setup'

export const FIRST_RUN_STEPS: readonly FirstRunStep[] = ['setup']

/** True once the welcome was finished, skipped, or not needed. */
export const $welcomeDone = persistentAtom<boolean>(WELCOME_DONE_KEY, INSTALL_KIND === 'existing', Codecs.bool)

/** Set when the user asks to see the welcome again. Not persisted: a restart ends the replay. */
export const $welcomeReplay = atom(false)

export const $firstRunStep = atom<FirstRunStep>('setup')

/** Whether the welcome flow owns the screen right now. */
export const $firstRunActive = computed([$welcomeDone, $welcomeReplay], (done, replay) => !done || replay)

export const $agentName = atom('')

export function goToFirstRunStep(step: FirstRunStep) {
  $firstRunStep.set(step)
}

export function nextFirstRunStep() {
  const i = FIRST_RUN_STEPS.indexOf($firstRunStep.get())
  $firstRunStep.set(FIRST_RUN_STEPS[Math.min(FIRST_RUN_STEPS.length - 1, i + 1)])
}

export function previousFirstRunStep() {
  const i = FIRST_RUN_STEPS.indexOf($firstRunStep.get())
  $firstRunStep.set(FIRST_RUN_STEPS[Math.max(0, i - 1)])
}

/** Finish (or skip out of) the welcome for good. */
export function finishFirstRun() {
  $welcomeDone.set(true)
  $welcomeReplay.set(false)
  $firstRunStep.set('setup')
}

/** Reopen the welcome from Settings or the command palette. */
export function showWelcomeAgain() {
  $firstRunStep.set('setup')
  $welcomeReplay.set(true)
}

/** Existing profile detected while the welcome was about to show: just step aside. */
export function markWelcomeNotNeeded() {
  if (!$welcomeDone.get()) {
    $welcomeDone.set(true)
  }
}

// ── Extras: calm by default for new installs, untouched for existing ones ──

const extraDefault = INSTALL_KIND === 'existing'

/** The floating pet (and its pop-out). */
export const $extraPet = persistentAtom<boolean>('clover.desktop.extra.pet', extraDefault, Codecs.bool)
/** The animated leaf-glyph art behind the chat. */
export const $extraBackdrop = persistentAtom<boolean>('clover.desktop.extra.backdrop', extraDefault, Codecs.bool)
/** The star map page (memory + skills graph). */
export const $extraStarmap = persistentAtom<boolean>('clover.desktop.extra.starmap', extraDefault, Codecs.bool)
/** The HUD chat bar and its in-game overlay. */
export const $extraHud = persistentAtom<boolean>('clover.desktop.extra.hud', extraDefault, Codecs.bool)
/** Every titlebar button (layout editor, swap sides, haptics). Off = just sidebar + settings. */
export const $extraWindowControls = persistentAtom<boolean>(
  'clover.desktop.extra.windowControls',
  extraDefault,
  Codecs.bool
)

/** New-install calm chrome: plain sidebar, no grid, tucked controls. Existing users keep their layout. */
export const $calmDesktop = atom<boolean>(INSTALL_KIND === 'new')

/** The backend already has a working setup: this is not a new install after all. */
export function adoptExistingProfile() {
  writeKey(INSTALL_KIND_KEY, 'existing')
  $calmDesktop.set(false)
  $extraPet.set(true)
  $extraStarmap.set(true)
  $extraHud.set(true)
  $extraBackdrop.set(true)
  $extraWindowControls.set(true)
  markWelcomeNotNeeded()
}
