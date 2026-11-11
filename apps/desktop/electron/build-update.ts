/**
 * Newer-build detection, idle gating and relaunch strategy for the desktop app.
 *
 * `clover update` rebuilds the packaged app in place, but a running desktop
 * keeps executing the code it started with (the app survives gateway restarts
 * since 2.0.8). This module decides — purely, with every input injected —
 *
 *  1. whether the build on disk is newer than the one running
 *     (`install-stamp.json`: commit + builtAt, written by every `npm run build`),
 *  2. whether it is a calm moment to restart (no phone turn, no /desk/stream
 *     viewer, no recent input, no chat mid-turn, no update in flight), and
 *  3. how to restart so the app comes back the way it was started.
 *
 * Electron wiring lives in main.ts; nothing here touches Electron or the disk.
 */

export interface BuildIdentity {
  builtAt: null | string
  commit: string
}

/** Quiet period with no user input before the app may restart itself. */
export const BUILD_UPDATE_IDLE_MS = 60_000
/** A phone turn counts as streaming while chat data moved this recently. */
export const RELAY_TURN_RECENT_MS = 30_000
export const BUILD_UPDATE_POLL_MS = 30_000

export function sameBuild(a: BuildIdentity | null, b: BuildIdentity | null): boolean {
  return Boolean(a && b && a.commit === b.commit && (a.builtAt ?? null) === (b.builtAt ?? null))
}

/**
 * True only on positive proof that the installed build differs from the
 * running one. A missing/unreadable installed stamp (mid-rebuild, deleted
 * directory) or a missing running stamp (dev run) is never "newer": a torn
 * read must not make the app restart into a half-written tree.
 */
export function isNewerBuildInstalled(running: BuildIdentity | null, installed: BuildIdentity | null): boolean {
  if (!running || !installed) {
    return false
  }

  if (/^0{7,40}$/.test(installed.commit)) {
    // Fallback (non-git) stamps carry a fake commit; builtAt is the only signal.
    return Boolean(installed.builtAt && installed.builtAt !== running.builtAt)
  }

  return !sameBuild(running, installed)
}

export interface RelayActivity {
  chatChannels: number
  deskChannels: number
  httpJobs: number
  lastChatDataAt: number
}

export type IdleBlocker = 'active-chat' | 'desk-viewer' | 'recent-input' | 'relay-turn' | 'update-in-flight'

export interface IdleInputs {
  activeChatCount: number
  idleMs?: number
  lastInputAt: number
  now: number
  relay: RelayActivity | null
  updateInFlight: boolean
}

/** First reason the app must NOT restart right now, or null when it is idle. */
export function idleBlocker(inputs: IdleInputs): IdleBlocker | null {
  const idleMs = inputs.idleMs ?? BUILD_UPDATE_IDLE_MS

  if (inputs.updateInFlight) {
    return 'update-in-flight'
  }

  if (inputs.activeChatCount > 0) {
    return 'active-chat'
  }

  const relay = inputs.relay

  if (relay) {
    if (relay.deskChannels > 0) {
      return 'desk-viewer'
    }

    if (relay.httpJobs > 0 || (relay.chatChannels > 0 && inputs.now - relay.lastChatDataAt < RELAY_TURN_RECENT_MS)) {
      return 'relay-turn'
    }
  }

  if (inputs.now - inputs.lastInputAt < idleMs) {
    return 'recent-input'
  }

  return null
}

export type RelaunchStrategy =
  | { kind: 'app-relaunch' }
  | { kind: 'systemd-restart'; unit: string }

/**
 * A process supervised by a systemd *user* service dies with its unit's
 * cgroup, so a child spawned by `app.relaunch()` would be killed the moment we
 * exit. Restarting the unit itself keeps the unit's ExecStart, environment and
 * supervision. Everything else (plain process, macOS app, Windows, a scope)
 * uses Electron's own relaunch, which starts the same executable with the same
 * arguments after this process exits.
 */
export function resolveRelaunchStrategy(input: {
  cgroup: null | string
  env: Record<string, string | undefined>
  platform: NodeJS.Platform
}): RelaunchStrategy {
  if (input.platform !== 'linux' || !input.env.INVOCATION_ID || !input.cgroup) {
    return { kind: 'app-relaunch' }
  }

  for (const line of input.cgroup.split('\n')) {
    const path = line.split(':').pop()?.trim() ?? ''

    if (!/\/user@\d+\.service\//.test(path)) {
      continue
    }

    const leaf = path.split('/').filter(Boolean).pop() ?? ''

    if (/^[A-Za-z0-9:_.\\@-]+\.service$/.test(leaf)) {
      return { kind: 'systemd-restart', unit: leaf }
    }
  }

  return { kind: 'app-relaunch' }
}

export interface BuildUpdateControllerDeps {
  activeChatCount: () => number
  lastInputAt: () => number
  log: (message: string) => void
  now?: () => number
  /** Notify renderers that a restart is waiting (calm in-app fallback). */
  offerRestart: (offered: boolean) => void
  readInstalled: () => BuildIdentity | null
  relay: () => null | RelayActivity
  relaunch: () => Promise<void> | void
  running: BuildIdentity | null
  updateInFlight: () => boolean
}

/**
 * One tick = "is there a newer build, and may we restart into it now?".
 * While a newer build waits behind a blocker the restart is offered in-app;
 * the controller keeps re-checking so it relaunches itself at the next idle
 * moment without anyone clicking.
 */
export function createBuildUpdateController(deps: BuildUpdateControllerDeps) {
  const now = deps.now ?? Date.now
  let relaunching = false
  let offered = false

  const setOffered = (next: boolean) => {
    if (next !== offered) {
      offered = next
      deps.offerRestart(next)
    }
  }

  async function restartNow(): Promise<boolean> {
    if (relaunching) {
      return false
    }

    relaunching = true

    try {
      await deps.relaunch()

      return true
    } catch (error) {
      relaunching = false
      deps.log(`[build-update] relaunch failed: ${error instanceof Error ? error.message : String(error)}`)

      return false
    }
  }

  return {
    /** User clicked "Restart" — explicit consent overrides the idle gate. */
    restartNow,
    async tick(): Promise<'idle-relaunch' | 'none' | 'offered' | 'relaunching'> {
      if (relaunching) {
        return 'relaunching'
      }

      if (!isNewerBuildInstalled(deps.running, deps.readInstalled())) {
        setOffered(false)

        return 'none'
      }

      const blocker = idleBlocker({
        activeChatCount: deps.activeChatCount(),
        lastInputAt: deps.lastInputAt(),
        now: now(),
        relay: deps.relay(),
        updateInFlight: deps.updateInFlight()
      })

      if (blocker) {
        setOffered(true)

        return 'offered'
      }

      deps.log('[build-update] newer build installed and app is idle; restarting into it')

      return (await restartNow()) ? 'idle-relaunch' : 'offered'
    },
    isOffered: () => offered
  }
}
