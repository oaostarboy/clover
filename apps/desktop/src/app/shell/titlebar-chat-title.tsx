import { useStore } from '@nanostores/react'

import { $selectedStoredSessionId, $sessions } from '@/store/session'

import { TITLEBAR_HEIGHT } from './titlebar'

/** Title of the chat on screen, or null on a fresh chat (nothing to name yet). */
export function chatTitleFor(
  sessions: readonly { id: string; preview?: null | string; title?: null | string }[],
  selectedId: null | string
): null | string {
  const hit = selectedId ? sessions.find(s => s.id === selectedId) : undefined

  return hit ? (hit.title?.trim() || hit.preview?.trim() || null) : null
}

/**
 * T3's thin top bar carries one thing: the name of the thread. Same here, a
 * single quiet line centred in the titlebar strip. No buttons of its own, and
 * it never takes a click: the strip stays a drag region.
 */
export function TitlebarChatTitle() {
  const sessions = useStore($sessions)
  const selected = useStore($selectedStoredSessionId)
  const title = chatTitleFor(sessions, selected)

  if (!title) {
    return null
  }

  return (
    <div
      className="pointer-events-none fixed inset-x-0 top-0 z-30 flex items-center justify-center px-40"
      data-slot="titlebar-chat-title"
      style={{ height: TITLEBAR_HEIGHT }}
    >
      <span className="min-w-0 max-w-[32rem] truncate text-[0.8125rem] text-(--ui-text-secondary)">{title}</span>
    </div>
  )
}
