/**
 * Renderer half of the desktop self-restart: reports user input (so the main
 * process can wait for ~60 s of quiet) and shows a calm "Restart to update"
 * toast when a newer build is installed but the app is busy.
 * Imported for its side effect from `main.tsx`.
 */

import { translateNow } from '@/i18n'
import { dismissNotification, notify } from '@/store/notifications'

export const BUILD_UPDATE_TOAST_ID = 'build-update-restart'
const ACTIVITY_THROTTLE_MS = 5_000

export function showRestartToUpdate(restart: () => void) {
  notify({
    action: { label: translateNow('notifications.restartToUpdateAction'), onClick: restart },
    durationMs: 0,
    id: BUILD_UPDATE_TOAST_ID,
    kind: 'info',
    message: translateNow('notifications.restartToUpdateMessage'),
    title: translateNow('notifications.restartToUpdate')
  })
}

export function installBuildUpdateBridge(
  win: { addEventListener: Window['addEventListener']; cloverDesktop?: Window['cloverDesktop'] },
  now: () => number = Date.now
) {
  const api = win.cloverDesktop
  let lastSent = 0

  const report = () => {
    const t = now()

    if (t - lastSent >= ACTIVITY_THROTTLE_MS) {
      lastSent = t
      api?.reportUserActivity?.()
    }
  }

  for (const type of ['keydown', 'pointerdown', 'wheel', 'pointermove'] as const) {
    win.addEventListener(type, report, { capture: true, passive: true })
  }

  return api?.buildUpdate?.onOffer(offered => {
    if (offered) {
      showRestartToUpdate(() => void api.buildUpdate?.restart())
    } else {
      dismissNotification(BUILD_UPDATE_TOAST_ID)
    }
  })
}

if (typeof window !== 'undefined') {
  installBuildUpdateBridge(window)
}
