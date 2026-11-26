import type { SubagentProgress, SubagentStatus } from '../types.js'

// One calm row per helper, like the chat card: `● task · 12s`.
// Marks are plain glyphs + a word for anything not working/done, so the
// card reads the same with NO_COLOR and on a narrow terminal.

export type HelperKind = 'done' | 'failed' | 'stopped' | 'waiting' | 'working'

export const HELPER_MARK: Record<HelperKind, string> = {
  done: '✓',
  failed: '✕',
  stopped: '◌',
  waiting: '○',
  working: '●'
}

const KIND_WORD: Record<HelperKind, string> = {
  done: 'done',
  failed: 'failed',
  stopped: 'stopped',
  waiting: 'waiting',
  working: 'working'
}

const KIND_ORDER: HelperKind[] = ['working', 'waiting', 'done', 'failed', 'stopped']

export const helperKind = (status: SubagentStatus): HelperKind =>
  status === 'completed'
    ? 'done'
    : status === 'failed' || status === 'error' || status === 'timeout'
      ? 'failed'
      : status === 'interrupted'
        ? 'stopped'
        : status === 'queued'
          ? 'waiting'
          : 'working'

export const fmtHelperTime = (seconds: number): string => {
  const s = Math.max(0, Math.floor(seconds))

  if (s < 60) {
    return `${s}s`
  }

  const m = Math.floor(s / 60)

  if (m < 10) {
    return `${m}m${String(s % 60).padStart(2, '0')}s`
  }

  if (m < 60) {
    return `${m}m`
  }

  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}m`
}

export const cutTask = (text: string, max: number): string => {
  const flat = text.replace(/\s+/g, ' ').trim()

  if (flat.length <= max) {
    return flat
  }

  const cut = flat.slice(0, Math.max(1, max - 1))
  const space = cut.lastIndexOf(' ')

  return `${(space >= max / 2 ? cut.slice(0, space) : cut).replace(/[\s,;:-]+$/, '')}…`
}

/** `3 helpers · 1 working · 1 done · 1 failed` (one helper: `1 helper · working`). */
export function helperSummary(items: readonly Pick<SubagentProgress, 'status'>[]): string {
  const kinds = items.map(i => helperKind(i.status))
  const head = `${kinds.length} helper${kinds.length === 1 ? '' : 's'}`

  if (kinds.length === 1) {
    return `${head} · ${KIND_WORD[kinds[0]!]}`
  }

  const parts = KIND_ORDER.flatMap(k => {
    const n = kinds.filter(x => x === k).length

    return n ? [`${n} ${KIND_WORD[k]}`] : []
  })

  return [head, ...parts].join(' · ')
}

export interface HelperRow {
  id: string
  kind: HelperKind
  reason?: string
  text: string
}

/** Row text without colour: mark, task, status word (failed/stopped/waiting only), timer. */
export function helperRow(item: SubagentProgress, now: number, taskMax = 34): HelperRow {
  const kind = helperKind(item.status)
  const task = cutTask(item.goal || `helper ${item.index + 1}`, taskMax)
  const secs = item.durationSeconds ?? (item.startedAt ? (now - item.startedAt) / 1000 : undefined)
  const bits = [task]

  if (kind === 'failed' || kind === 'stopped' || kind === 'waiting') {
    bits.push(KIND_WORD[kind])
  }

  if (secs !== undefined && kind !== 'waiting') {
    bits.push(fmtHelperTime(secs))
  }

  const reason =
    kind === 'failed'
      ? cutTask((item.summary || '').replace(/^plain summary:\s*/i, '') || 'hit an error', 40)
      : undefined

  return { id: item.id, kind, reason, text: `${HELPER_MARK[kind]} ${bits.join(' · ')}` }
}

/** Most rows shown; finished-OK ones fold into `+N done`, failures and live helpers always stay. */
export const HELPER_ROWS_SHOWN = 12

export function helperRows(items: readonly SubagentProgress[], now: number, taskMax = 34) {
  const rows = items.map(i => helperRow(i, now, taskMax))
  let over = rows.length - HELPER_ROWS_SHOWN
  const hide = new Set<number>()

  for (const wanted of ['done', 'working'] as const) {
    for (let i = rows.length - 1; i >= 0 && over > 0; i--) {
      if (!hide.has(i) && rows[i]!.kind === wanted) {
        hide.add(i)
        over--
      }
    }
  }

  const shown = rows.filter((_, i) => !hide.has(i))
  const hidden = [...hide].map(i => rows[i]!)

  return {
    more: hidden.length ? `+${hidden.length} ${hidden.every(r => r.kind === 'done') ? 'done' : 'more'}` : '',
    shown
  }
}
