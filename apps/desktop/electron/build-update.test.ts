import { describe, expect, it, vi } from 'vitest'

import {
  BUILD_UPDATE_IDLE_MS,
  createBuildUpdateController,
  idleBlocker,
  isNewerBuildInstalled,
  RELAY_TURN_RECENT_MS,
  type RelayActivity,
  resolveRelaunchStrategy
} from './build-update'

const OLD = { commit: 'a'.repeat(40), builtAt: '2026-11-11T10:00:00Z' }
const NEW = { commit: 'b'.repeat(40), builtAt: '2026-11-11T12:00:00Z' }
const QUIET: RelayActivity = { chatChannels: 0, deskChannels: 0, httpJobs: 0, lastChatDataAt: 0 }
const NOW = 10_000_000

const idle = (over: Partial<Parameters<typeof idleBlocker>[0]> = {}) =>
  idleBlocker({
    activeChatCount: 0,
    lastInputAt: NOW - BUILD_UPDATE_IDLE_MS - 1,
    now: NOW,
    relay: QUIET,
    updateInFlight: false,
    ...over
  })

describe('isNewerBuildInstalled', () => {
  it('detects a different commit or build time', () => {
    expect(isNewerBuildInstalled(OLD, NEW)).toBe(true)
    expect(isNewerBuildInstalled(OLD, { ...OLD, builtAt: '2026-11-11T11:00:00Z' })).toBe(true)
  })

  it('is false for the same build', () => {
    expect(isNewerBuildInstalled(OLD, { ...OLD })).toBe(false)
  })

  it('never fires on an unreadable installed stamp or a dev run without a stamp', () => {
    expect(isNewerBuildInstalled(OLD, null)).toBe(false)
    expect(isNewerBuildInstalled(null, NEW)).toBe(false)
  })

  it('uses builtAt for fallback (non-git) stamps', () => {
    const fake = '0'.repeat(40)

    expect(isNewerBuildInstalled({ commit: fake, builtAt: 'x' }, { commit: fake, builtAt: 'x' })).toBe(false)
    expect(isNewerBuildInstalled({ commit: fake, builtAt: 'x' }, { commit: fake, builtAt: 'y' })).toBe(true)
  })
})

describe('idleBlocker', () => {
  it('is null when everything is quiet', () => {
    expect(idle()).toBeNull()
  })

  it('blocks on a phone turn streaming through the relay', () => {
    expect(idle({ relay: { ...QUIET, chatChannels: 1, lastChatDataAt: NOW - 1000 } })).toBe('relay-turn')
    expect(idle({ relay: { ...QUIET, httpJobs: 1 } })).toBe('relay-turn')
  })

  it('does not block on an idle, connected phone', () => {
    expect(idle({ relay: { ...QUIET, chatChannels: 1, lastChatDataAt: NOW - RELAY_TURN_RECENT_MS - 1 } })).toBeNull()
  })

  it('blocks on an active /desk/stream viewer', () => {
    expect(idle({ relay: { ...QUIET, deskChannels: 1 } })).toBe('desk-viewer')
  })

  it('blocks on recent user input', () => {
    expect(idle({ lastInputAt: NOW - 5_000 })).toBe('recent-input')
  })

  it('blocks on an in-progress chat in the window', () => {
    expect(idle({ activeChatCount: 1 })).toBe('active-chat')
  })

  it('blocks while an in-app update is applying', () => {
    expect(idle({ updateInFlight: true })).toBe('update-in-flight')
  })
})

describe('resolveRelaunchStrategy', () => {
  const userCgroup = '0::/user.slice/user-1000.slice/user@1000.service/app.slice/clover-desktop.service\n'

  it('restarts the user systemd unit the app runs in', () => {
    expect(resolveRelaunchStrategy({ cgroup: userCgroup, env: { INVOCATION_ID: 'x' }, platform: 'linux' })).toEqual({
      kind: 'systemd-restart',
      unit: 'clover-desktop.service'
    })
  })

  it('uses Electron relaunch for plain processes, scopes, other OSes', () => {
    const scope = '0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-x.scope\n'
    const plain = { kind: 'app-relaunch' }

    expect(resolveRelaunchStrategy({ cgroup: userCgroup, env: {}, platform: 'linux' })).toEqual(plain)
    expect(resolveRelaunchStrategy({ cgroup: scope, env: { INVOCATION_ID: 'x' }, platform: 'linux' })).toEqual(plain)
    expect(resolveRelaunchStrategy({ cgroup: null, env: { INVOCATION_ID: 'x' }, platform: 'linux' })).toEqual(plain)
    expect(resolveRelaunchStrategy({ cgroup: userCgroup, env: { INVOCATION_ID: 'x' }, platform: 'darwin' })).toEqual(plain)
    expect(resolveRelaunchStrategy({ cgroup: userCgroup, env: { INVOCATION_ID: 'x' }, platform: 'win32' })).toEqual(plain)
  })
})

function harness(over: { installed?: typeof NEW | null; relay?: RelayActivity; chats?: number; lastInputAt?: number } = {}) {
  const relaunch = vi.fn(async () => {})
  const offerRestart = vi.fn()
  const state = { installed: over.installed === undefined ? NEW : over.installed, relay: over.relay ?? QUIET, chats: over.chats ?? 0, lastInputAt: over.lastInputAt ?? 0 }

  const controller = createBuildUpdateController({
    activeChatCount: () => state.chats,
    lastInputAt: () => state.lastInputAt,
    log: () => {},
    now: () => NOW,
    offerRestart,
    readInstalled: () => state.installed,
    relay: () => state.relay,
    relaunch,
    running: OLD,
    updateInFlight: () => false
  })

  return { controller, offerRestart, relaunch, state }
}

describe('createBuildUpdateController', () => {
  it('relaunches once into the new build when idle', async () => {
    const { controller, relaunch, offerRestart } = harness()

    expect(await controller.tick()).toBe('idle-relaunch')
    expect(await controller.tick()).toBe('relaunching')
    expect(relaunch).toHaveBeenCalledTimes(1)
    expect(offerRestart).not.toHaveBeenCalled()
  })

  it('does nothing when the installed build is the running build', async () => {
    const { controller, relaunch } = harness({ installed: OLD })

    expect(await controller.tick()).toBe('none')
    expect(relaunch).not.toHaveBeenCalled()
  })

  it.each([
    ['relay turn', { relay: { ...QUIET, chatChannels: 1, lastChatDataAt: NOW } }],
    ['stream viewer', { relay: { ...QUIET, deskChannels: 1 } }],
    ['recent input', { lastInputAt: NOW - 1000 }],
    ['active chat', { chats: 1 }]
  ])('offers "Restart to update" instead of relaunching on %s', async (_name, over) => {
    const { controller, relaunch, offerRestart } = harness(over)

    expect(await controller.tick()).toBe('offered')
    expect(relaunch).not.toHaveBeenCalled()
    expect(offerRestart).toHaveBeenCalledWith(true)
    expect(controller.isOffered()).toBe(true)
  })

  it('relaunches by itself once the blocker clears, and the click path overrides the gate', async () => {
    const { controller, relaunch, state, offerRestart } = harness({ chats: 1 })

    expect(await controller.tick()).toBe('offered')
    state.chats = 0
    expect(await controller.tick()).toBe('idle-relaunch')
    expect(relaunch).toHaveBeenCalledTimes(1)

    const second = harness({ chats: 1 })

    expect(await second.controller.restartNow()).toBe(true)
    expect(second.relaunch).toHaveBeenCalledTimes(1)
    expect(offerRestart).toHaveBeenCalledTimes(1)
  })

  it('withdraws the offer when the new build is no longer pending and survives a failed relaunch', async () => {
    const { controller, state, offerRestart, relaunch } = harness({ chats: 1 })

    await controller.tick()
    state.installed = OLD
    await controller.tick()
    expect(offerRestart).toHaveBeenLastCalledWith(false)

    relaunch.mockRejectedValueOnce(new Error('spawn failed'))
    state.installed = NEW
    state.chats = 0
    expect(await controller.tick()).toBe('offered')
    expect(await controller.tick()).toBe('idle-relaunch')
  })
})
