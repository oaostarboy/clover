import { afterEach, describe, expect, it, vi } from 'vitest'

import { $notifications } from '@/store/notifications'

import { BUILD_UPDATE_TOAST_ID, installBuildUpdateBridge } from './build-update'

function fakeWindow() {
  const listeners = new Map<string, () => void>()

  let offer: (offered: boolean) => void = () => {}
  const restart = vi.fn(async () => true)
  const reportUserActivity = vi.fn()

  return {
    emit: (type: string) => listeners.get(type)?.(),
    offer: (v: boolean) => offer(v),
    reportUserActivity,
    restart,
    win: {
      addEventListener: ((type: string, fn: () => void) => void listeners.set(type, fn)) as Window['addEventListener'],
      cloverDesktop: {
        buildUpdate: { onOffer: (cb: (o: boolean) => void) => ((offer = cb), () => {}), restart },
        reportUserActivity
      } as unknown as Window['cloverDesktop']
    }
  }
}

afterEach(() => $notifications.set([]))

describe('build update bridge', () => {
  it('throttles user-activity reports', () => {
    const f = fakeWindow()
    let t = 1_000_000
    installBuildUpdateBridge(f.win, () => t)
    f.emit('keydown')
    f.emit('pointermove')
    expect(f.reportUserActivity).toHaveBeenCalledTimes(1)
    t += 6_000
    f.emit('keydown')
    expect(f.reportUserActivity).toHaveBeenCalledTimes(2)
  })

  it('shows a Restart to update toast whose action restarts, and clears when withdrawn', () => {
    const f = fakeWindow()
    installBuildUpdateBridge(f.win)
    f.offer(true)

    const toast = $notifications.get().find(n => n.id === BUILD_UPDATE_TOAST_ID)
    expect(toast?.title).toBe('Restart to update')
    toast?.action?.onClick()
    expect(f.restart).toHaveBeenCalledTimes(1)

    f.offer(false)
    expect($notifications.get().some(n => n.id === BUILD_UPDATE_TOAST_ID)).toBe(false)
  })
})
